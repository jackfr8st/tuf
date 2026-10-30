"""Text normalization, deterministic keyword matching, and privacy sanitization."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache

_HASHTAG = re.compile(r"(?<!\w)#([\w-]{2,40})", re.UNICODE)
_BRACKET = re.compile(r"\[([^\[\]]{2,40})\]")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_URL = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_PHONE = re.compile(r"\+?\d[\d\s().-]{7,}\d")
_LONG_DIGITS = re.compile(r"\d{6,}")
_MENTION = re.compile(r"(?<!\w)@\w+")


def normalize(text: str) -> str:
    """Lowercase, strip accents-insensitive punctuation noise, drop apostrophes."""
    text = unicodedata.normalize("NFKC", text).casefold()
    text = text.replace("’", "").replace("'", "")
    return re.sub(r"\s+", " ", text).strip()


def hashtags(text: str) -> list[str]:
    return [m.group(1) for m in _HASHTAG.finditer(text)]


def bracket_tags(text: str) -> list[str]:
    return [m.group(1).strip() for m in _BRACKET.finditer(text)]


def clean_title(description: str) -> str:
    """Human title: description minus #hashtags and [bracket] tags."""
    text = _BRACKET.sub(" ", _HASHTAG.sub(" ", description))
    text = re.sub(r"\s+", " ", text).strip(" -–—:|")
    if not text:
        return "Untitled expense"
    text = text[:120]
    return text[0].upper() + text[1:] if text[0].islower() else text


def fingerprint(description: str) -> str:
    """Stable key for 'the same kind of expense' (used for learned classifications)."""
    text = normalize(_BRACKET.sub(" ", _HASHTAG.sub(" ", description)))
    text = re.sub(r"[^a-z\s&+]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def classification_key(description: str, splitwise_category: str | None) -> str:
    """Changes only when inputs that affect classification change (cache invalidation)."""
    raw = f"{normalize(description)}|{(splitwise_category or '').casefold()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def sanitize_for_ai(description: str, max_len: int = 160) -> str:
    """Remove data an AI classifier never needs: emails, URLs, phone/card-like numbers,
    @mentions. Amounts and participants are never sent at all."""
    text = _EMAIL.sub("[email]", description)
    text = _URL.sub("[link]", text)
    text = _PHONE.sub("[number]", text)
    text = _LONG_DIGITS.sub("[number]", text)
    text = _MENTION.sub("[person]", text)
    text = "".join(ch for ch in text if ch.isprintable())
    return re.sub(r"\s+", " ", text).strip()[:max_len]


@lru_cache(maxsize=4096)
def _pattern(keyword: str) -> re.Pattern[str]:
    return re.compile(r"(?<![\w])" + re.escape(normalize(keyword)) + r"(?![\w])")


@dataclass(frozen=True)
class KeywordHit:
    keyword: str
    target: object


def best_keyword_match(text: str, candidates: Iterable[tuple[str, object]]) -> KeywordHit | None:
    """Longest matching keyword wins ('uber eats' beats 'uber'); ties keep first."""
    norm = normalize(text)
    best: KeywordHit | None = None
    for keyword, target in candidates:
        if not keyword or (best and len(keyword) <= len(best.keyword)):
            continue
        if _pattern(keyword).search(norm):
            best = KeywordHit(keyword, target)
    return best
