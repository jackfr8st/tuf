"""Trip identification.

A trip is an *analytical dimension*, not a category: each expense links to at most one trip
through a relation on the same Expenses row, so it is counted once overall, once in its
category, and once in its trip. No duplicate financial records are ever created.

Matching precedence (explicit identifiers first, AI last):

1. A Trips row lists the expense's Splitwise group (by id or exact name).
2. The group name matches the trip-group pattern, e.g. ``Trip: Big Sur 2026``.
3. A ``#hashtag`` in the description matches a trip's name or tags.
4. A ``[Bracketed]`` tag in the description matches a trip's name or tags.
5. Unknown hashtags / bracket tags create a new trip (configurable).
6. (Optional) an AI trip hint, accepted only if it names an existing trip.

Matching uses exact slug equality, so "Big Sur 2026" and "Big Sur 2025" never collide.
Transportation or lodging expenses are never assumed to be trips.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from tuf.categorization.text import bracket_tags, hashtags

DEFAULT_GROUP_PATTERN = r"^\s*(?:trip|travel|vacation)\s*[:\-–—]\s*(?P<name>.+?)\s*$"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.casefold())


@dataclass
class TripDef:
    name: str
    start: date | None = None
    end: date | None = None
    budget: Decimal | None = None
    group_refs: set[str] = field(default_factory=set)  # group ids or names, as entered
    tags: set[str] = field(default_factory=set)  # extra aliases/hashtags (without '#')
    page_id: str | None = None

    @property
    def key(self) -> str:
        return slug(self.name)

    def slugs(self) -> set[str]:
        return {self.key} | {slug(t) for t in self.tags if slug(t)}


@dataclass(frozen=True)
class TripMatch:
    key: str
    name: str
    source: str  # "group-mapping" | "group-name" | "hashtag" | "bracket" | "ai"
    is_new: bool = False
    group_id: int | None = None


class TripMatcher:
    def __init__(
        self,
        trips: list[TripDef],
        *,
        group_pattern: str = DEFAULT_GROUP_PATTERN,
        auto_create_from_tags: bool = True,
    ) -> None:
        self.trips: dict[str, TripDef] = {t.key: t for t in trips if t.key}
        self._pattern = re.compile(group_pattern, re.IGNORECASE)
        self.auto_create = auto_create_from_tags
        self._by_slug: dict[str, TripDef] = {}
        self._by_group: dict[str, TripDef] = {}
        for t in self.trips.values():
            self._index(t)

    def _index(self, t: TripDef) -> None:
        for s in t.slugs():
            self._by_slug.setdefault(s, t)
        for ref in t.group_refs:
            self._by_group[ref.strip().casefold()] = t

    def is_trip_group(self, group_id: int, group_name: str | None) -> bool:
        """Side-effect-free check (never creates trips)."""
        if str(group_id) in self._by_group or (group_name or "").casefold() in self._by_group:
            return True
        return bool(group_name and self._pattern.match(group_name))

    def names(self) -> tuple[str, ...]:
        return tuple(t.name for t in self.trips.values())

    def match(
        self,
        description: str,
        group_id: int | None,
        group_name: str | None,
        ai_hint: str | None = None,
    ) -> TripMatch | None:
        if group_id is not None:
            for ref in (str(group_id), (group_name or "").casefold()):
                t = self._by_group.get(ref.strip().casefold()) if ref else None
                if t:
                    return TripMatch(t.key, t.name, "group-mapping", group_id=group_id)
            if group_name:
                m = self._pattern.match(group_name)
                if m and m.groupdict().get("name"):
                    return self._resolve(
                        m.group("name").strip(), "group-name", group_id=group_id, allow_create=True
                    )
        for tag in hashtags(description):
            found = self._resolve(tag, "hashtag", allow_create=self.auto_create)
            if found:
                return found
        for tag in bracket_tags(description):
            found = self._resolve(tag, "bracket", allow_create=self.auto_create)
            if found:
                return found
        if ai_hint:
            t = self._by_slug.get(slug(ai_hint))
            if t:
                return TripMatch(t.key, t.name, "ai")
        return None

    def _resolve(
        self, name: str, source: str, *, allow_create: bool, group_id: int | None = None
    ) -> TripMatch | None:
        s = slug(name)
        if not s:
            return None
        t = self._by_slug.get(s)
        if t:
            return TripMatch(t.key, t.name, source, group_id=group_id)
        if not allow_create:
            return None
        new = TripDef(name=name[:80])
        if group_id is not None:
            new.group_refs.add(str(group_id))
        self.trips[new.key] = new
        self._index(new)
        return TripMatch(new.key, new.name, source, is_new=True, group_id=group_id)
