"""Trip identification."""

from tuf.trips.engine import TripDef, TripMatcher, slug


def test_dedicated_trip_group_by_name_pattern():
    m = TripMatcher([]).match("Dinner", 55, "Trip: Big Sur 2026")
    assert m and m.name == "Big Sur 2026" and m.is_new and m.source == "group-name"


def test_user_group_mapping_by_id_or_name():
    trips = [TripDef("Tahoe Ski", group_refs={"77"}), TripDef("Vegas", group_refs={"Bachelor"})]
    matcher = TripMatcher(trips)
    assert matcher.match("Lift", 77, "Ski crew").name == "Tahoe Ski"
    assert matcher.match("Show", 12, "bachelor").name == "Vegas"


def test_hashtag_and_bracket_tags():
    matcher = TripMatcher([TripDef("Big Sur 2026", tags={"bigsur26"})])
    assert matcher.match("Gas #bigsur2026", None, None).name == "Big Sur 2026"
    assert matcher.match("Gas #BigSur26", None, None).name == "Big Sur 2026"
    assert matcher.match("Tacos [Big Sur 2026]", None, None).name == "Big Sur 2026"


def test_unknown_hashtag_creates_trip_once_and_reuses_it():
    matcher = TripMatcher([])
    first = matcher.match("Hotel #nyc2027", None, None)
    second = matcher.match("Pizza #NYC2027", None, None)
    assert first.is_new and not second.is_new and first.key == second.key


def test_auto_create_can_be_disabled():
    assert TripMatcher([], auto_create_from_tags=False).match("x #nyc", None, None) is None


def test_multiple_and_similar_trip_names_do_not_collide():
    matcher = TripMatcher([TripDef("Big Sur 2025"), TripDef("Big Sur 2026"), TripDef("Big Sur")])
    assert matcher.match("x #bigsur2025", None, None).name == "Big Sur 2025"
    assert matcher.match("x #bigsur2026", None, None).name == "Big Sur 2026"
    assert matcher.match("x #bigsur", None, None).name == "Big Sur"


def test_expenses_outside_trips_and_no_category_assumption():
    matcher = TripMatcher([TripDef("Big Sur 2026")])
    assert matcher.match("Uber to work", None, None) is None
    assert matcher.match("Hotel downtown", 3, "Roommates") is None


def test_ai_hint_only_matches_existing():
    matcher = TripMatcher([TripDef("Big Sur 2026")])
    assert matcher.match("gas", None, None, ai_hint="big sur 2026").source == "ai"
    assert matcher.match("gas", None, None, ai_hint="Mars 2099") is None


def test_explicit_group_beats_hashtag():
    matcher = TripMatcher([TripDef("Tahoe", group_refs={"9"}), TripDef("Vegas")])
    assert matcher.match("x #vegas", 9, "Ski").name == "Tahoe"


def test_slug():
    assert slug("Big Sur – 2026!") == "bigsur2026"
