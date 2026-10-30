"""Dynamic subcategories from recurring patterns.

Each classified expense may carry a ``suggested_subcategory`` (from the built-in latent
catalog, the Splitwise category, or AI). When the same proposal recurs on at least
``min_occurrences`` distinct active expenses, TUF proposes it as a real subcategory
(status ``Suggested``, or ``Active`` if auto-approve is enabled). A single purchase never
creates a category. Duplicate and synonymous names are merged; rejected names are never
proposed again.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from tuf.categorization.taxonomy import (
    OTHER,
    Taxonomy,
    catalog_icon,
    clean_category_name,
    name_tokens,
)
from tuf.models import LedgerEntry, SyncStatus


@dataclass(frozen=True)
class SubcategorySuggestion:
    parent: str
    name: str
    occurrences: int
    icon: str


def find_suggestions(
    entries: Iterable[LedgerEntry], taxonomy: Taxonomy, min_occurrences: int = 3
) -> list[SubcategorySuggestion]:
    groups: dict[tuple[str, frozenset[str]], Counter[str]] = defaultdict(Counter)
    for e in entries:
        if e.status != SyncStatus.ACTIVE or not e.suggested_subcategory or e.primary == "":
            continue
        name = clean_category_name(e.suggested_subcategory)
        if not name:
            continue
        groups[(e.primary, name_tokens(name))][name] += 1

    out: list[SubcategorySuggestion] = []
    for (parent, _tokens), spellings in groups.items():
        total = sum(spellings.values())
        if total < min_occurrences or taxonomy.resolve_primary(parent) is None:
            continue
        name = spellings.most_common(1)[0][0]
        if taxonomy.find_subcategory(parent, name) is not None:
            continue  # already exists as Active/Suggested/Rejected (incl. synonyms)
        if parent == OTHER and total < min_occurrences * 2:
            continue  # be conservative about splitting the catch-all bucket
        out.append(SubcategorySuggestion(parent, name, total, catalog_icon(parent, name)))
    return sorted(out, key=lambda s: (-s.occurrences, s.parent, s.name))
