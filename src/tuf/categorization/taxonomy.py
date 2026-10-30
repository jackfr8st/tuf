"""Category taxonomy: built-in defaults plus the user's live Categories database.

Two-level hierarchy: ``Primary Category -> Subcategory``. Subcategories have a status:

* ``Active``   - used for classification and reporting.
* ``Suggested`` - proposed by TUF from recurring patterns; awaiting user review.
* ``Rejected`` - the user declined it; never suggested again.

The built-in catalog contains *latent* subcategories (``seeded=False``) whose keywords are
known but which only become real categories once recurring purchases justify them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

OTHER = "Other"


class CategoryStatus:
    ACTIVE = "Active"
    SUGGESTED = "Suggested"
    REJECTED = "Rejected"
    ARCHIVED = "Archived"  # retired category: kept for history, no longer used


@dataclass(frozen=True)
class CatalogEntry:
    name: str
    parent: str | None  # None => primary category
    icon: str
    keywords: tuple[str, ...]
    seeded: bool = True


def _kw(text: str) -> tuple[str, ...]:
    return tuple(k.strip() for k in text.split(",") if k.strip())


PRIMARIES: tuple[CatalogEntry, ...] = (
    CatalogEntry(
        "Dining Out",
        None,
        "🍽️",
        _kw("restaurant, dinner, lunch, brunch, breakfast, street food, food truck, takeout"),
    ),
    CatalogEntry(
        "Groceries",
        None,
        "🛒",
        _kw(
            "groceries, grocery, supermarket, produce, "
            "farmers market, target, ralphs, trader joes, whole foods, safeway, costco, "
            "walmart, kroger, vons, sprouts, aldi, h mart, 99 ranch, instacart, food 4 less"
        ),
    ),
    CatalogEntry(
        "Snacks",
        None,
        "🍿",
        _kw(
            "snack, snacks, chips, candy, chocolate, 7-eleven, 7 eleven, convenience store, vending"
        ),
    ),
    CatalogEntry("Entertainment", None, "🎬", _kw("entertainment, subscription")),
    CatalogEntry("Shopping", None, "🛍️", _kw("shopping, mall, purchase")),
    CatalogEntry("Transportation", None, "🚗", _kw("transport, transportation, travel fare")),
    CatalogEntry(
        "Stay",
        None,
        "🏨",
        _kw(
            "hotel, airbnb, hostel, motel, lodging, resort, vrbo, "
            "booking.com, marriott, hilton, hyatt, accommodation"
        ),
    ),
    CatalogEntry("Health & Insurance", None, "🩺", _kw("health, medical")),
    CatalogEntry(OTHER, None, "📦", ()),
)

SUBCATEGORIES: tuple[CatalogEntry, ...] = (
    # Dining Out
    CatalogEntry(
        "Coffee & Cafes",
        "Dining Out",
        "☕",
        _kw(
            "coffee, cafe, café, latte, espresso, "
            "cappuccino, starbucks, dunkin, peets, blue bottle, philz, coffee bean"
        ),
    ),
    CatalogEntry(
        "Restaurants",
        "Dining Out",
        "🍝",
        _kw(
            "restaurant, dinner, lunch, brunch, "
            "sushi, pizza, ramen, taco, tacos, pho, bbq, burger, diner, bistro, grill, thai, "
            "indian food, korean bbq, dim sum"
        ),
    ),
    CatalogEntry(
        "Fast Food",
        "Dining Out",
        "🍔",
        _kw(
            "mcdonalds, chipotle, taco bell, in-n-out, "
            "in n out, wendys, kfc, burger king, panda express, chick-fil-a, five guys, "
            "jack in the box, popeyes"
        ),
        seeded=False,
    ),
    CatalogEntry(
        "Desserts & Ice Cream",
        "Dining Out",
        "🍦",
        _kw(
            "ice cream, gelato, dessert, "
            "froyo, frozen yogurt, donut, donuts, bakery, cake, cookies, boba, bubble tea"
        ),
        seeded=False,
    ),
    CatalogEntry(
        "Takeout & Delivery",
        "Dining Out",
        "🥡",
        _kw("doordash, uber eats, ubereats, grubhub, postmates, takeout, delivery"),
        seeded=False,
    ),
    CatalogEntry(
        "Bars & Drinks",
        "Dining Out",
        "🍻",
        _kw("bar, pub, beer, drinks, cocktails, brewery, wine bar"),
        seeded=False,
    ),
    # Entertainment
    CatalogEntry(
        "Streaming Subscriptions",
        "Entertainment",
        "📺",
        _kw(
            "netflix, spotify, "
            "youtube music, youtube premium, hulu, disney+, disney plus, hbo, hbo max, "
            "apple music, prime video, apple tv, paramount+, peacock, crunchyroll"
        ),
    ),
    CatalogEntry(
        "Movies", "Entertainment", "🎟️", _kw("movie, movies, cinema, amc, regal, imax"), seeded=False
    ),
    CatalogEntry(
        "Activities",
        "Entertainment",
        "🎳",
        _kw(
            "bowling, hiking, hike, climbing, "
            "museum, concert, ticketmaster, zoo, aquarium, arcade, golf, karaoke, "
            "escape room, park entry, theme park"
        ),
        seeded=False,
    ),
    CatalogEntry(
        "Games",
        "Entertainment",
        "🎮",
        _kw("steam, playstation, xbox, nintendo, video game"),
        seeded=False,
    ),
    # Shopping
    CatalogEntry(
        "Clothing",
        "Shopping",
        "👕",
        _kw(
            "clothes, clothing, shirt, t-shirt, jeans, "
            "shoes, sneakers, jacket, uniqlo, h&m, zara, nike, adidas"
        ),
    ),
    CatalogEntry(
        "Electronics",
        "Shopping",
        "💻",
        _kw(
            "best buy, apple store, laptop, phone case, "
            "headphones, charger, electronics, airpods, monitor, keyboard"
        ),
        seeded=False,
    ),
    CatalogEntry(
        "Gifts", "Shopping", "🎁", _kw("gift, gifts, present, birthday gift"), seeded=False
    ),
    CatalogEntry(
        "Online Shopping", "Shopping", "📦", _kw("amazon, ebay, etsy, temu, shein"), seeded=False
    ),
    CatalogEntry(
        "Home & Household",
        "Shopping",
        "🏠",
        _kw("ikea, household, furniture, cleaning supplies, toiletries, detergent, paper towels"),
        seeded=False,
    ),
    # Transportation
    CatalogEntry(
        "Rideshare", "Transportation", "🚕", _kw("uber, lyft, ola, waymo, taxi, cab, rideshare")
    ),
    CatalogEntry(
        "Flights",
        "Transportation",
        "✈️",
        _kw(
            "flight, flights, airline, airfare, "
            "plane ticket, united airlines, delta air, southwest, american airlines, "
            "alaska airlines, jetblue, spirit airlines, air india, indigo"
        ),
    ),
    CatalogEntry(
        "Public Transit",
        "Transportation",
        "🚇",
        _kw("metro, bus, train, caltrain, bart, amtrak, tap card, transit, muni, clipper"),
        seeded=False,
    ),
    CatalogEntry(
        "Car Rental",
        "Transportation",
        "🚙",
        _kw("car rental, rental car, hertz, enterprise rent, avis, turo, zipcar"),
        seeded=False,
    ),
    CatalogEntry(
        "Fuel",
        "Transportation",
        "⛽",
        _kw("gas station, fuel, petrol, gas, chevron, arco, exxon, shell gas"),
        seeded=False,
    ),
    CatalogEntry(
        "Ferries & Boats",
        "Transportation",
        "⛴️",
        _kw("ferry, boat, cruise, kayak rental"),
        seeded=False,
    ),
    CatalogEntry(
        "Parking & Tolls", "Transportation", "🅿️", _kw("parking, toll, tolls"), seeded=False
    ),
    # Health & Insurance
    CatalogEntry(
        "Health Insurance",
        "Health & Insurance",
        "🛡️",
        _kw("health insurance, insurance premium, insurance"),
    ),
    CatalogEntry(
        "Pharmacy",
        "Health & Insurance",
        "💊",
        _kw("pharmacy, cvs, walgreens, rite aid, medicine, meds, prescription"),
        seeded=False,
    ),
    CatalogEntry(
        "Medical",
        "Health & Insurance",
        "🏥",
        _kw("doctor, clinic, hospital, dental, dentist, therapy, copay, urgent care, eye exam"),
        seeded=False,
    ),
    CatalogEntry(
        "Fitness", "Health & Insurance", "🏋️", _kw("gym, fitness, yoga, pilates"), seeded=False
    ),
    # Other
    CatalogEntry("Rent", OTHER, "🏠", _kw("rent, lease"), seeded=False),
    CatalogEntry(
        "Utilities",
        OTHER,
        "💡",
        _kw(
            "electricity, electric bill, water bill, internet, "
            "wifi, utilities, gas bill, phone bill, trash"
        ),
        seeded=False,
    ),
)

# Well-known brand keywords -> display merchant name (deterministic merchant extraction).
MERCHANTS: dict[str, str] = {
    "starbucks": "Starbucks",
    "dunkin": "Dunkin'",
    "peets": "Peet's Coffee",
    "blue bottle": "Blue Bottle Coffee",
    "philz": "Philz Coffee",
    "target": "Target",
    "ralphs": "Ralphs",
    "trader joes": "Trader Joe's",
    "whole foods": "Whole Foods",
    "safeway": "Safeway",
    "costco": "Costco",
    "walmart": "Walmart",
    "kroger": "Kroger",
    "vons": "Vons",
    "sprouts": "Sprouts",
    "aldi": "Aldi",
    "h mart": "H Mart",
    "99 ranch": "99 Ranch",
    "instacart": "Instacart",
    "7-eleven": "7-Eleven",
    "7 eleven": "7-Eleven",
    "netflix": "Netflix",
    "spotify": "Spotify",
    "youtube music": "YouTube Music",
    "youtube premium": "YouTube Premium",
    "hulu": "Hulu",
    "disney+": "Disney+",
    "disney plus": "Disney+",
    "hbo max": "HBO Max",
    "hbo": "HBO",
    "apple music": "Apple Music",
    "prime video": "Prime Video",
    "uber eats": "Uber Eats",
    "ubereats": "Uber Eats",
    "doordash": "DoorDash",
    "grubhub": "Grubhub",
    "uber": "Uber",
    "lyft": "Lyft",
    "ola": "Ola",
    "waymo": "Waymo",
    "airbnb": "Airbnb",
    "vrbo": "Vrbo",
    "marriott": "Marriott",
    "hilton": "Hilton",
    "hyatt": "Hyatt",
    "amazon": "Amazon",
    "ikea": "IKEA",
    "uniqlo": "Uniqlo",
    "zara": "Zara",
    "nike": "Nike",
    "adidas": "Adidas",
    "best buy": "Best Buy",
    "cvs": "CVS",
    "walgreens": "Walgreens",
    "chipotle": "Chipotle",
    "mcdonalds": "McDonald's",
    "in-n-out": "In-N-Out",
    "in n out": "In-N-Out",
    "taco bell": "Taco Bell",
    "panda express": "Panda Express",
    "amc": "AMC",
    "southwest": "Southwest Airlines",
    "united airlines": "United Airlines",
    "delta air": "Delta Air Lines",
    "jetblue": "JetBlue",
    "amtrak": "Amtrak",
    "caltrain": "Caltrain",
    "bart": "BART",
    "hertz": "Hertz",
    "turo": "Turo",
    "zipcar": "Zipcar",
    "chevron": "Chevron",
    "arco": "ARCO",
    "steam": "Steam",
}

# Splitwise's own category names -> (primary, subcategory). "General"/"Other" are ignored
# because they are Splitwise's defaults and carry no signal.
SPLITWISE_CATEGORY_MAP: dict[str, tuple[str, str | None]] = {
    "dining out": ("Dining Out", "Restaurants"),
    "groceries": ("Groceries", None),
    "liquor": ("Dining Out", "Bars & Drinks"),
    "bus/train": ("Transportation", "Public Transit"),
    "car": ("Transportation", None),
    "gas/fuel": ("Transportation", "Fuel"),
    "hotel": ("Stay", None),
    "parking": ("Transportation", "Parking & Tolls"),
    "plane": ("Transportation", "Flights"),
    "taxi": ("Transportation", "Rideshare"),
    "bicycle": ("Transportation", None),
    "games": ("Entertainment", "Games"),
    "movies": ("Entertainment", "Movies"),
    "music": ("Entertainment", None),
    "sports": ("Entertainment", "Activities"),
    "electronics": ("Shopping", "Electronics"),
    "furniture": ("Shopping", "Home & Household"),
    "household supplies": ("Shopping", "Home & Household"),
    "rent": (OTHER, "Rent"),
    "mortgage": (OTHER, "Rent"),
    "clothing": ("Shopping", "Clothing"),
    "gifts": ("Shopping", "Gifts"),
    "insurance": ("Health & Insurance", "Health Insurance"),
    "medical expenses": ("Health & Insurance", "Medical"),
    "electricity": (OTHER, "Utilities"),
    "heat/gas": (OTHER, "Utilities"),
    "water": (OTHER, "Utilities"),
    "tv/phone/internet": (OTHER, "Utilities"),
    "trash": (OTHER, "Utilities"),
    "cleaning": (OTHER, "Utilities"),
}


# --------------------------------------------------------------------------- names

_SYNONYMS = {
    "cafe": "coffee",
    "café": "coffee",
    "cafes": "coffee",
    "taxi": "rideshare",
    "cab": "rideshare",
    "ride": "rideshare",
    "rides": "rideshare",
    "streaming": "streaming",
    "subscription": "subscription",
    "eating": "dining",
    "restaurant": "restaurant",
    "airfare": "flight",
    "lodging": "stay",
    "hotel": "stay",
    "grocery": "grocery",
    "medicine": "pharmacy",
}
_STOPWORDS = {
    "and",
    "the",
    "of",
    "shop",
    "shops",
    "store",
    "stores",
    "purchase",
    "purchases",
    "expense",
    "expenses",
}


def normalize_name(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip()).casefold()


def name_tokens(name: str) -> frozenset[str]:
    """Canonical token set used to detect duplicate / synonymous category names."""
    text = normalize_name(name).replace("&", " and ")
    tokens = set()
    for raw in re.findall(r"[a-z0-9é+]+", text):
        tok = _SYNONYMS.get(raw, raw)
        if tok in _STOPWORDS:
            continue
        if len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss"):
            tok = tok[:-1]
        tokens.add(_SYNONYMS.get(tok, tok))
    return frozenset(tokens)


def similar_names(a: str, b: str) -> bool:
    ta, tb = name_tokens(a), name_tokens(b)
    if not ta or not tb:
        return normalize_name(a) == normalize_name(b)
    if ta == tb:
        return True
    small, large = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    return small <= large and len(large) <= 2


_SAFE_NAME = re.compile(r"^[\w&+'/,.\- ]{2,40}$", re.UNICODE)


def clean_category_name(name: str | None) -> str | None:
    """Validate a proposed category name (from AI or users). Returns a tidy name or None."""
    if not name:
        return None
    name = re.sub(r"\s+", " ", str(name)).strip()
    if not _SAFE_NAME.match(name):
        return None
    return " ".join(
        w if w.isupper() or w in {"&", "+"} else w[:1].upper() + w[1:] for w in name.split(" ")
    )


# --------------------------------------------------------------------------- runtime taxonomy


@dataclass
class CategoryDef:
    name: str
    parent: str | None
    icon: str = ""
    monthly_budget: Decimal | None = None
    rules: tuple[str, ...] = ()
    status: str = CategoryStatus.ACTIVE
    page_id: str | None = None

    @property
    def usable(self) -> bool:
        return self.status == CategoryStatus.ACTIVE


@dataclass
class Taxonomy:
    primaries: dict[str, CategoryDef] = field(default_factory=dict)
    subcategories: dict[tuple[str, str], CategoryDef] = field(default_factory=dict)

    @classmethod
    def defaults(cls) -> Taxonomy:
        tax = cls()
        for p in PRIMARIES:
            tax.primaries[p.name] = CategoryDef(p.name, None, p.icon)
        for s in SUBCATEGORIES:
            if s.seeded:
                tax.subcategories[(s.parent or OTHER, s.name)] = CategoryDef(
                    s.name, s.parent, s.icon
                )
        return tax

    def add(self, cat: CategoryDef) -> None:
        if cat.parent:
            self.subcategories[(cat.parent, cat.name)] = cat
        else:
            self.primaries[cat.name] = cat

    def primary_names(self) -> list[str]:
        names = [n for n, c in self.primaries.items() if c.usable]
        return names if OTHER in names else [*names, OTHER]

    def resolve_primary(self, name: str | None) -> str | None:
        if not name:
            return None
        for existing, cat in self.primaries.items():
            if cat.usable and normalize_name(existing) == normalize_name(name):
                return existing
        return OTHER if normalize_name(name) == normalize_name(OTHER) else None

    def find_subcategory(self, parent: str, name: str) -> CategoryDef | None:
        """Exact-or-synonym lookup among a parent's subcategories (any status)."""
        for (p, _n), cat in self.subcategories.items():
            if p == parent and similar_names(cat.name, name):
                return cat
        return None

    def active_subcategory(self, parent: str, name: str | None) -> str | None:
        if not name:
            return None
        cat = self.find_subcategory(parent, name)
        return cat.name if cat and cat.usable else None

    def active_subcategories(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for (parent, name), cat in self.subcategories.items():
            if cat.usable:
                out.setdefault(parent, []).append(name)
        return out

    def icon_for(self, primary: str, sub: str | None = None) -> str:
        if sub:
            cat = self.subcategories.get((primary, sub))
            if cat and cat.icon:
                return cat.icon
        p = self.primaries.get(primary)
        if p and p.icon:
            return p.icon
        return next((c.icon for c in PRIMARIES if c.name == primary), "📦")


def catalog_icon(parent: str, name: str) -> str:
    for s in SUBCATEGORIES:
        if s.parent == parent and similar_names(s.name, name):
            return s.icon
    return "🏷️"
