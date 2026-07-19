"""Curated text banks for the dev-data seeder.

Kept dependency-free on purpose (no Faker): a hand-picked bank gives us exactly
the shapes we want to stress in the UI — one-word items, long paragraphs,
embedded line breaks, Markdown, emoji and non-ASCII — while staying fully
deterministic under a seeded ``random.Random``. ``seed_dev_data`` samples from
these lists; it never hard-codes content itself.
"""

# ── Checklist names ────────────────────────────────────────────────────────────
LIST_NAMES = [
    "Groceries",
    "Weekend trip packing",
    "Sprint 42 backlog",
    "Home renovation",
    "Books to read",
    "Garden to-do",
    "Birthday party plan",
    "Bug triage",
    "Camping gear",
    "Meal prep",
    "Move-out checklist",
    "Guitar practice",
    "Onboarding new hire",
    "Wishlist",
    "Recipes to try",
    "Car maintenance",
    "Conference talk prep",
    "Wedding planning",
    "Houseplants care",
    "Side project ideas",
    "Winter clothes",
    "Pharmacy run",
    "Kids school supplies",
    "Podcast episodes",
    "Refactor targets",
    "Grocery — Aldi",
    "Grocery — farmers market",
    "Quarterly goals",
    "Board game night",
    "Emergency kit",
]

# Names with unicode / emoji, to prove the UI handles them.
LIST_NAMES_FANCY = [
    "🛒 Wocheneinkauf",
    "Reise nach München ✈️",
    "日本語の勉強",
    "Café ☕ supplies",
    "Проект «Весна»",
    "🎧 Playlist ideas",
    "Größenübersicht 📏",
    "Ελληνικά μαθήματα",
]

# ── Short items (one to a few words) ───────────────────────────────────────────
SHORT_ITEMS = [
    "Milk",
    "Eggs",
    "Bananas",
    "Coffee beans",
    "Toothpaste",
    "Batteries (AA)",
    "Duct tape",
    "Olive oil",
    "Dish soap",
    "Sourdough",
    "Call the dentist",
    "Water the ferns",
    "Book flights",
    "Charge headphones",
    "Return library book",
    "Sunscreen",
    "Trash bags",
    "Printer paper",
    "Cat food",
    "Light bulbs",
    "Umbrella",
    "Passport photos",
    "Cash for market",
    "Phone charger",
    "Ibuprofen",
    "Rice",
    "Garlic",
    "Sponges",
    "Stamps",
    "Zip ties",
]

# ── Long items (a full sentence or two) ────────────────────────────────────────
LONG_ITEMS = [
    "Compare three moving companies and get written quotes before Friday so we can lock in a date and not pay the peak weekend surcharge.",
    "Ask the landlord in writing whether the deposit covers the small paint scuffs in the hallway or if we should touch those up ourselves.",
    "Research whether the noise-cancelling headphones are worth the extra cost over the mid-range pair, and read at least two long-term reviews.",
    "Draft the follow-up email to the vendor summarising what we agreed on the call: delivery window, unit price, and the return policy for damaged goods.",
    "Pick up the prescription, but double-check with the pharmacist about taking it together with the current allergy medication.",
    "Reorganise the pantry so the oldest tins are at the front; throw out anything past its date and note what needs restocking.",
    "Walk through every room and list the light fixtures that need new bulbs, including the awkward one over the stairs that needs the tall ladder.",
    "Read the tenancy agreement section on subletting carefully before replying to the person who asked about the spare room.",
]

# ── Multi-line items (embedded newlines) ───────────────────────────────────────
MULTILINE_ITEMS = [
    "Costco run:\n- paper towels\n- rotisserie chicken\n- the big bag of coffee",
    "Ask Sam about:\n1. the weekend schedule\n2. who's driving\n3. splitting the cabin cost",
    "Two sizes needed —\nshirt: M\npants: 32/32",
    "Route:\nhome → bakery → post office → back\n(avoid the bridge, it's closed)",
    "Recipe tweaks:\nless salt\ndouble the garlic\nbake 5 min longer",
]

# ── Markdown items (card notes now render Markdown) ────────────────────────────
MARKDOWN_ITEMS = [
    "Buy **oat milk** (not soy this time)",
    "Read the [setup guide](https://example.com/setup) before the call",
    "Fix the `null` check in `parseConfig()`",
    "Priorities:\n\n1. **deposit**\n2. movers\n3. _address change_",
    "> Remember: the shop closes early on Sundays",
    "`TODO` — split this into two smaller tasks",
    "Compare:\n\n- Option A — cheaper\n- Option B — **faster**",
]

# ── Emoji / non-ASCII items ────────────────────────────────────────────────────
FANCY_ITEMS = [
    "🥑 Avocados (ripe!)",
    "Café ☕ pods",
    "Gerätespülmittel 🧽",
    "🌱 Basilikum-Samen",
    "Приправы 🧂",
    "🔋 USB-C cables ×3",
    "Ελιές 🫒",
    "🎂 Birthday candles",
]

# ── Checklist descriptions / notes (the card's `text` field) ───────────────────
NOTES_SHORT = [
    "For the trip next week.",
    "Don't forget the receipt.",
    "Shared with the whole team.",
    "Ongoing — add as you think of things.",
    "Time-sensitive!",
]

NOTES_MARKDOWN = [
    "Anything **bold** is urgent. Cross off as you go.\n\nSee the [wiki](https://example.com/wiki) for context.",
    "## Ground rules\n\n- one owner per item\n- link the PR when done\n- `blocked` items go to the bottom",
    "Budget: **$400** max.\n\n> Keep receipts for everything over $20.",
    "Split by aisle:\n\n1. produce\n2. dairy\n3. frozen",
]

NOTES_MULTILINE = [
    "Pickup window is 2–4pm.\nCall when you're 10 minutes out.\nGate code: 4412",
    "Keep this in order.\nTop three are the priority.\nRest is nice-to-have.",
]

# ── Label names (with an optional color, chosen by the seeder) ─────────────────
LABEL_NAMES = [
    "Urgent",
    "Home",
    "Work",
    "Shopping",
    "Ideas",
    "Waiting",
    "Someday",
    "Travel",
    "Health",
    "Finance",
    "Errands",
    "Reading",
]
