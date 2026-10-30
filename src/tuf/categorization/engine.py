"""Classification pipeline.

Priority (first match wins):

1. Manual: the user ticked "Category Locked" on the Notion row.
2. Rule: keywords from the user's Categories database ("Classification Rules").
3. Learned: an identical expense (same fingerprint) the user locked before.
4. Keyword: built-in deterministic keyword catalog.
5. Splitwise Category: the category the payer picked in Splitwise (if not "General").
6. AI: optional Gemini classification, cached on the Notion row by classification key.
7. Fallback: "Other".

Subcategories are only *assigned* when they are Active in the taxonomy; otherwise the
proposal is recorded as ``suggested_subcategory`` for review (see suggestions.py).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import cast

from tuf.categorization.ai import (
    AIClassifier,
    AIItem,
    AIQuotaExceeded,
    AIRequest,
    AIResult,
    AIUnavailable,
    parse_results,
)
from tuf.categorization.taxonomy import (
    MERCHANTS,
    OTHER,
    PRIMARIES,
    SPLITWISE_CATEGORY_MAP,
    SUBCATEGORIES,
    CategoryStatus,
    Taxonomy,
    clean_category_name,
)
from tuf.categorization.text import (
    best_keyword_match,
    classification_key,
    clean_title,
    fingerprint,
    sanitize_for_ai,
)
from tuf.models import Classification, ClassSource, LedgerEntry

log = logging.getLogger(__name__)

CONFIDENCE = {
    ClassSource.MANUAL: Decimal("1"),
    ClassSource.RULE: Decimal("1"),
    ClassSource.LEARNED: Decimal("0.95"),
    ClassSource.KEYWORD: Decimal("0.9"),
    ClassSource.SPLITWISE: Decimal("0.8"),
    ClassSource.PENDING: Decimal("0"),
    ClassSource.FALLBACK: Decimal("0"),
}


@dataclass(frozen=True)
class ClassifyInput:
    ref: str
    description: str
    splitwise_category: str | None = None
    context: str | None = None  # anonymized hint such as "trip group"
    existing: LedgerEntry | None = None

    @property
    def key(self) -> str:
        return classification_key(self.description, self.splitwise_category)


@dataclass
class AIStats:
    requests: int = 0
    items_sent: int = 0
    items_classified: int = 0
    failures: int = 0
    quota_exhausted: bool = False
    skipped_items: int = 0


@dataclass
class Learned:
    primary: str
    subcategory: str | None
    merchant: str | None


def learned_from(entries: Iterable[LedgerEntry]) -> dict[str, Learned]:
    """User-confirmed (locked) rows become reusable classifications, newest first."""
    out: dict[str, Learned] = {}
    for e in sorted(entries, key=lambda e: e.source_updated_at):
        if e.locked and e.description:
            fp = fingerprint(e.description)
            if fp:
                out[fp] = Learned(e.primary, e.subcategory, e.merchant)
    return out


@dataclass
class Categorizer:
    taxonomy: Taxonomy
    learned: dict[str, Learned] = field(default_factory=dict)
    ai: AIClassifier | None = None
    ai_batch_size: int = 20
    ai_max_requests: int = 5
    min_confidence: Decimal = Decimal("0.6")
    trip_names: tuple[str, ...] = ()
    use_cache: bool = True  # False => re-derive even unchanged AI/fallback results
    stats: AIStats = field(default_factory=AIStats)

    # ------------------------------------------------------------------ public

    def classify_all(self, inputs: list[ClassifyInput]) -> dict[str, Classification]:
        results: dict[str, Classification] = {}
        ai_queue: list[ClassifyInput] = []
        for inp in inputs:
            result = self.classify_without_ai(inp)
            if result is None:
                ai_queue.append(inp)
            else:
                results[inp.ref] = result
        results.update(self._classify_with_ai(ai_queue))
        return results

    def classify_without_ai(self, inp: ClassifyInput) -> Classification | None:
        """Deterministic stages. Returns None when the expense should go to AI."""
        title = clean_title(inp.description)
        existing = inp.existing

        if existing is not None and existing.locked:
            return Classification(
                existing.primary,
                existing.subcategory,
                existing.merchant,
                title,
                ClassSource.MANUAL,
                CONFIDENCE[ClassSource.MANUAL],
            )

        merchant = self._merchant(inp.description)
        for stage in (self._user_rule, self._learned, self._keyword, self._splitwise):
            hit = stage(inp)
            if hit is not None:
                primary, sub, source, learned_merchant = hit
                return self._finish(
                    primary, sub, learned_merchant or merchant, title, source, CONFIDENCE[source]
                )

        cached = (
            self.use_cache
            and existing is not None
            and existing.class_key == inp.key
            and existing.class_source in (ClassSource.AI, ClassSource.FALLBACK)
        )
        if cached and existing is not None:
            # Cached result for unchanged input: never re-ask the AI.
            proposal = existing.subcategory or existing.suggested_subcategory
            return self._finish(
                existing.primary,
                proposal,
                existing.merchant or merchant,
                existing.title or title,
                existing.class_source,
                existing.confidence,
            )

        if self.ai is None:
            return self._finish(
                OTHER, None, merchant, title, ClassSource.FALLBACK, CONFIDENCE[ClassSource.FALLBACK]
            )
        return None

    # ------------------------------------------------------------------ stages

    def _user_rule(self, inp: ClassifyInput) -> tuple[str, str | None, ClassSource, None] | None:
        candidates: list[tuple[str, object]] = []
        for cat in [*self.taxonomy.subcategories.values(), *self.taxonomy.primaries.values()]:
            if not cat.usable:
                continue
            target = (cat.parent, cat.name) if cat.parent else (cat.name, None)
            candidates.extend((kw, target) for kw in cat.rules)
        hit = best_keyword_match(inp.description, candidates)
        if hit is None:
            return None
        primary, sub = cast(tuple[str, str | None], hit.target)
        return primary, sub, ClassSource.RULE, None

    def _learned(
        self, inp: ClassifyInput
    ) -> tuple[str, str | None, ClassSource, str | None] | None:
        found = self.learned.get(fingerprint(inp.description))
        if found is None or self.taxonomy.resolve_primary(found.primary) is None:
            return None
        return found.primary, found.subcategory, ClassSource.LEARNED, found.merchant

    def _keyword(self, inp: ClassifyInput) -> tuple[str, str | None, ClassSource, None] | None:
        candidates: list[tuple[str, object]] = []
        for s in SUBCATEGORIES:
            candidates.extend((kw, (s.parent, s.name)) for kw in s.keywords)
        for p in PRIMARIES:
            candidates.extend((kw, (p.name, None)) for kw in p.keywords)
        hit = best_keyword_match(inp.description, candidates)
        if hit is None:
            return None
        primary, sub = cast(tuple[str, str | None], hit.target)
        if self.taxonomy.resolve_primary(primary) is None:
            return None  # the user removed this primary category
        return primary, sub, ClassSource.KEYWORD, None

    def _splitwise(self, inp: ClassifyInput) -> tuple[str, str | None, ClassSource, None] | None:
        if not inp.splitwise_category:
            return None
        mapped = SPLITWISE_CATEGORY_MAP.get(inp.splitwise_category.strip().casefold())
        if mapped is None or self.taxonomy.resolve_primary(mapped[0]) is None:
            return None
        return mapped[0], mapped[1], ClassSource.SPLITWISE, None

    @staticmethod
    def _merchant(description: str) -> str | None:
        hit = best_keyword_match(description, MERCHANTS.items())
        return str(hit.target) if hit else None

    def _finish(
        self,
        primary: str,
        proposed_sub: str | None,
        merchant: str | None,
        title: str,
        source: ClassSource,
        confidence: Decimal,
    ) -> Classification:
        """Gate the subcategory: assign only Active ones; record others as suggestions."""
        primary = self.taxonomy.resolve_primary(primary) or OTHER
        sub, suggested = None, None
        if proposed_sub:
            cat = self.taxonomy.find_subcategory(primary, proposed_sub)
            if cat is not None and cat.usable:
                sub = cat.name
            elif cat is None or cat.status != CategoryStatus.REJECTED:
                suggested = cat.name if cat is not None else proposed_sub
        return Classification(
            primary, sub, merchant, title, source, confidence, suggested_subcategory=suggested
        )

    # ------------------------------------------------------------------ AI

    def _classify_with_ai(self, queue: list[ClassifyInput]) -> dict[str, Classification]:
        out: dict[str, Classification] = {}
        if not queue:
            return out
        consecutive_failures = 0
        for start in range(0, len(queue), self.ai_batch_size):
            chunk = queue[start : start + self.ai_batch_size]
            can_call = (
                self.ai is not None
                and not self.stats.quota_exhausted
                and self.stats.requests < self.ai_max_requests
                and consecutive_failures < 2
            )
            if not can_call:
                self.stats.skipped_items += len(chunk)
                out.update({i.ref: self._pending(i) for i in chunk})
                continue
            try:
                out.update(self._ai_chunk(chunk))
                consecutive_failures = 0
            except AIQuotaExceeded:
                log.warning("AI quota exhausted; remaining expenses use rule-based fallback")
                self.stats.quota_exhausted = True
                self.stats.failures += 1
                out.update({i.ref: self._pending(i) for i in chunk})
            except AIUnavailable as exc:
                log.warning("AI classification unavailable: %s", exc)
                self.stats.failures += 1
                consecutive_failures += 1
                out.update({i.ref: self._pending(i) for i in chunk})
        return out

    def _ai_chunk(self, chunk: list[ClassifyInput]) -> dict[str, Classification]:
        assert self.ai is not None
        local = {f"e{i}": inp for i, inp in enumerate(chunk)}
        request = AIRequest(
            items=[
                AIItem(ref, sanitize_for_ai(inp.description), inp.context)
                for ref, inp in local.items()
            ],
            primaries=self.taxonomy.primary_names(),
            subcategories=self.taxonomy.active_subcategories(),
            trips=list(self.trip_names),
        )
        self.stats.requests += 1
        self.stats.items_sent += len(chunk)
        results = parse_results(self.ai.classify(request))
        out: dict[str, Classification] = {}
        for res in results:
            inp = local.pop(res.ref, None)
            if inp is None:
                continue  # unknown or duplicate ref: ignore
            out[inp.ref] = self._from_ai(inp, res)
            self.stats.items_classified += 1
        for inp in local.values():  # items the model skipped
            out[inp.ref] = self._pending(inp)
        return out

    def _from_ai(self, inp: ClassifyInput, res: AIResult) -> Classification:
        title = _safe_ai_title(res.clean_title, inp.description) or clean_title(inp.description)
        merchant = self._merchant(inp.description) or _safe_merchant(res.merchant)
        primary = self.taxonomy.resolve_primary(res.primary_category)
        confidence = res.confidence_decimal
        if primary is None or confidence < self.min_confidence:
            return self._finish(OTHER, None, merchant, title, ClassSource.FALLBACK, confidence)
        sub = clean_category_name(res.subcategory)
        if (
            sub
            and not res.suggest_new_subcategory
            and self.taxonomy.active_subcategory(primary, sub) is None
        ):
            sub = None  # claimed "existing" but isn't: discard rather than trust
        result = self._finish(primary, sub, merchant, title, ClassSource.AI, confidence)
        if res.trip_tag:
            match = next(
                (t for t in self.trip_names if t.casefold() == res.trip_tag.casefold()), None
            )
            result.trip_hint = match
        return result

    def _pending(self, inp: ClassifyInput) -> Classification:
        merchant = self._merchant(inp.description)
        return self._finish(
            OTHER,
            None,
            merchant,
            clean_title(inp.description),
            ClassSource.PENDING,
            CONFIDENCE[ClassSource.PENDING],
        )


_DIGITS = re.compile(r"\d+")


def _safe_ai_title(title: str | None, original: str) -> str | None:
    """Accept an AI title only if it is short and introduces no numbers (no invented amounts)."""
    if not title or len(title) > 80 or "\n" in title:
        return None
    if any(d not in original for d in _DIGITS.findall(title)):
        return None
    return title


def _safe_merchant(merchant: str | None) -> str | None:
    if not merchant or len(merchant) > 60 or not merchant.isprintable():
        return None
    return merchant
