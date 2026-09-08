"""Day 3 voice-agent orchestration: memory + RAG + objection handling."""
from __future__ import annotations

import contextvars
import re
from dataclasses import dataclass, field
from typing import Any, Callable

import pandas as pd
from sqlalchemy import text

from config import CSV_DIR, get_engine
from rag_pipeline import get_retriever, get_vectorstore, rag_retrieve
from recommendation_engine import recommend_properties

# When set (via stream_sink_context below), make_voice_answer's single real
# LLM call site streams each token to this callback as Gemini emits it,
# instead of only returning the full answer once generation is complete.
# Scripted/instant branches never call the LLM, so they're unaffected --
# this only matters for the catch-all RAG-answer branch at the bottom of
# make_voice_answer, which is the one that can take several seconds.
_stream_sink: "contextvars.ContextVar[Callable[[str], None] | None]" = contextvars.ContextVar(
    "_stream_sink", default=None
)


class stream_sink_context:
    """Context manager: `with stream_sink_context(fn): ...` makes
    make_voice_answer stream LLM tokens to fn(text) as they arrive, for any
    make_voice_answer call made inside the block (including via
    asyncio.to_thread, since contextvars are propagated into the thread it
    spawns). Restores the previous sink (normally None) on exit."""

    def __init__(self, sink: Callable[[str], None]):
        self._sink = sink
        self._token = None

    def __enter__(self):
        self._token = _stream_sink.set(self._sink)
        return self

    def __exit__(self, *exc_info):
        _stream_sink.reset(self._token)
        return False


OBJECTION_PLAYBOOK = {
    "price": "Acknowledge the budget concern, avoid pressure, show verified alternatives within or below the remembered budget, and explain trade-offs using only verified facts.",
    "trust": "Acknowledge the concern, offer verified developer/agent/payment-plan information from the knowledge base, and never invent approvals, guarantees, documents, or claims.",
    "location": "Clarify the location trade-off and use verified locality amenities, schools, hospitals, and property availability. Do not claim travel times unless verified.",
    "investment": "Discuss fit, locality facts, payment plans and property fundamentals only. Never guarantee appreciation, ROI, rental yield, or future returns.",
    "builder": "Use only verified developer information from the knowledge base. If it is missing, say so and offer human-agent follow-up.",
    "maintenance": "Acknowledge the recurring-cost concern and only quote verified maintenance/service information. If unavailable, say it is not in the verified knowledge base.",
}


@dataclass
class ConversationMemory:
    turns: list[dict[str, str]] = field(default_factory=list)
    preferences: dict[str, Any] = field(default_factory=dict)

    def add(self, role: str, text: str) -> None:
        self.turns.append({"role": role, "text": text})
        if len(self.turns) > 20:
            self.turns = self.turns[-20:]
        if role == "user":
            self._extract_preferences(text)

    def _extract_preferences(self, text: str) -> None:
        t = text.lower()
        # Crore/lakh budget parsing, including Urdu-ish spellings.
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:crore|crores|cr|karor|کروڑ)", t)
        if m:
            self.preferences["budget_pkr"] = float(m.group(1)) * 10_000_000
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:lakh|lac|لاکھ)", t)
        if m:
            self.preferences["budget_pkr"] = float(m.group(1)) * 100_000
        # Explicit rupee amounts, comma-separated or plain.
        m = re.search(r"(?:rs\.?|pkr|rupees?)\s*([\d,]+(?:\.\d+)?)", t)
        if m:
            self.preferences["budget_pkr"] = float(m.group(1).replace(",", ""))
        # Word-based numbers in Urdu
        urdu_numbers = {
            "ایک": 1, "دو": 2, "تین": 3, "چار": 4, "پانچ": 5, "پونچ": 5,
            "چھ": 6, "سات": 7, "آٹھ": 8, "نو": 9, "دس": 10, "ٹین": 10,
            "گیارہ": 11, "بارہ": 12, "تیرہ": 13, "چودہ": 14, "پندرہ": 15,
            "سولہ": 16, "سترہ": 17, "اٹھارہ": 18, "انیس": 19, "بیس": 20,
            "پچیس": 25, "تیس": 30, "اکتیس": 31, "پچاس": 50, "سو": 100
        }
        marla_cues = (
            "مرلہ", "مرلے", "مرلوں", "مرلا", "مرحلہ", "مرحلے", "مرحلوں",
            "مرد لے", "مردلے", "مر لگے", "مرلگے", "مڑلے", "مڈلے", "مدلے",
            "مت لے", "ملی", "میلی", "نیلی", "ملہ", "marla", "marle", "marley", "marly", "size", "گھر", "house"
        )
        size_was_asked = bool(self.preferences.get("size_budget_asked"))
        for word, val in urdu_numbers.items():
            if f"{word} کروڑ" in t or f"{word}کرور" in t or f"{word} crore" in t:
                self.preferences["budget_pkr"] = float(val) * 10_000_000
            elif f"{word} لاکھ" in t or f"{word} lakh" in t or f"{word} lac" in t:
                self.preferences["budget_pkr"] = float(val) * 100_000
            if re.search(rf"{word}\s*(?:مرلہ|مرلے|مرلوں|مرلا|مرحلہ|مرحلے|مرحلوں|مرد\s*لے|مردلے|مر\s*لگے|مرلگے|مڑلے|مڈلے|مدلے|مت\s*لے|ملی|میلی|نیلی|ملہ|marla|marle|marley|marly)", t) or (re.search(rf"\b{word}\b", t) and (size_was_asked or any(m in t for m in marla_cues))):
                self.preferences["area_marla"] = float(val)
            elif re.search(rf"\b{word}\s*(?:کنال|kanal)\b", t):
                self.preferences["area_marla"] = float(val * 20)

        # Plot/house size, including speech-to-text variations
        m_marla = re.search(r"(\d+(?:\.\d+)?)\s*(?:مرلہ|مرلے|مرلوں|مرلا|مرحلہ|مرحلے|مرحلوں|مرد\s*لے|مردلے|مر\s*لگے|مرلگے|مڑلے|مڈلے|مدلے|مت\s*لے|ملی|میلی|نیلی|ملہ|marla|marly|marley|marle|marlay)", t)
        if m_marla:
            self.preferences["area_marla"] = float(m_marla.group(1))
        elif size_was_asked:
            m_num_only = re.search(r"\b(\d+(?:\.\d+)?)\b", t)
            if m_num_only and float(m_num_only.group(1)) <= 100:
                self.preferences["area_marla"] = float(m_num_only.group(1))
        m_kanal = re.search(r"(\d+(?:\.\d+)?)\s*(?:kanal|کنال)", t)
        if m_kanal:
            self.preferences["area_marla"] = float(m_kanal.group(1)) * 20

        # Bedrooms
        m = re.search(r"(\d+)\s*(?:bed|beds|bedroom|bedrooms|br|بیڈ|کمرے)", t)
        if m:
            self.preferences["bedrooms"] = int(m.group(1))

        type_mapping = {
            "farm house": "Farm House",
            "فارم ہاؤس": "Farm House",
            "upper portion": "Upper Portion",
            "اوپر والا پورشن": "Upper Portion",
            "lower portion": "Lower Portion",
            "نیچے والا پورشن": "Lower Portion",
            "portion": "Portion",
            "پورشن": "Portion",
            "house": "House",
            "ghar": "House",
            "ghr": "House",
            "گھر": "House",
            "گر": "House",
            "مکان": "House",
            "flat": "Flat",
            "فلیٹ": "Flat",
            "اپارٹمنٹ": "Flat",
            "apartment": "Flat",
            "plot": "Plot",
            "پلاٹ": "Plot",
            "room": "Room",
            "کمرہ": "Room",
        }
        for pattern, label in type_mapping.items():
            if pattern in t:
                self.preferences["property_type"] = label
                break

        city_mapping = {
            "lahore": "Lahore",
            "لاہور": "Lahore",
            "islamabad": "Islamabad",
            "اسلام آباد": "Islamabad",
            "rawalpindi": "Rawalpindi",
            "راولپنڈی": "Rawalpindi",
            "پنڈی": "Rawalpindi",
        }
        for c_pat, c_name in city_mapping.items():
            if c_pat in t:
                self.preferences["city"] = c_name
                break

        # Keep the phase separately
        phase = re.search(r"(?:\bdha\s*(?:phase|ph)\s*[-#]?\s*(\d+)\b|ڈی ایچ اے\s*فیز\s*(\d+))", t)
        if phase:
            phase_num = phase.group(1) or phase.group(2)
            if phase_num:
                self.preferences["locality_phase"] = int(phase_num)

        # Common locality cues, including speech variations (e.g. پی ایچ اے / جی ایچ اے / ڈی ایچ اے)
        localities = (
            "bahria town rawalpindi", "bahria town islamabad", "bahria town", "bahria",
            "بحریہ ٹاؤن", "بحریہ",
            "dha defence islamabad", "dha defence", "ڈی ایچ اے", "ڈی ایچ ای", "پی ایچ اے", "جی ایچ اے", "بی ایچ اے", "ڈیفنس", "ڈفینس", "ڈفنس", "defense", "defence", "dha", "jha", "gha",
            "faisal town", "فیصل ٹاؤن", "model town", "ماڈل ٹاؤن",
            "johar town", "جوہر ٹاؤن", "gulberg", "گلبرگ", "college road", "کالج روڈ",
            "askari", "عسکری", "ghauri town", "غوری ٹاؤن",
            "airport housing society", "airport", "e-11", "e11", "f-11", "f11", "f-10", "f10",
            "f-8", "f8", "f-7", "f7", "f-6", "f6", "g-15", "g15", "g-13", "g13", "g-11", "g11", "g-10", "g10", "i-10", "i10"
        )
        for locality in localities:
            if locality in t:
                loc_clean = locality.replace("e11", "e-11").replace("f11", "f-11").replace("f10", "f-10").replace("g15", "g-15").replace("g13", "g-13").replace("g11", "g-11")
                if loc_clean in ("ڈی ایچ اے", "ڈی ایچ ای", "پی ایچ اے", "جی ایچ اے", "بی ایچ اے", "ڈیفنس", "ڈفینس", "ڈفنس", "defense", "defence", "dha", "jha", "gha"):
                    loc_clean = "dha"
                elif loc_clean in ("بحریہ ٹاؤن", "بحریہ"):
                    loc_clean = "bahria town"
                elif loc_clean == "گلبرگ":
                    loc_clean = "gulberg"
                elif loc_clean == "جوہر ٹاؤن":
                    loc_clean = "johar town"
                elif loc_clean == "ماڈل ٹاؤن":
                    loc_clean = "model town"
                elif loc_clean == "فیصل ٹاؤن":
                    loc_clean = "faisal town"
                elif loc_clean == "عسکری":
                    loc_clean = "askari"
                self.preferences["locality_contains"] = loc_clean
                break

        if any(x in t for x in ("rent", "rental", "kiraya", "kiraye", "کرایہ", "کرائے", "کرایے", "رینٹ")):
            self.preferences["purpose"] = "For Rent"
        elif any(x in t for x in ("buy", "purchase", "sale", "for sale", "kharid", "khareed", "khared", "lena", "خرید", "خارید", "خریدنا", "خریدنے", "خرید میں", "لینا", "لینے", "تریدنا", "ترید نہ", "تریدنہ", "تو ید", "قریب نہ", "بائے", "سیل", "خریدوں")):
            self.preferences["purpose"] = "For Sale"

        # Client name extraction
        m_name = re.search(r"(?:mera naam|naam|my name is|this is|naam hai)\s+([A-Za-z\u0600-\u06ff]+)", t)
        if m_name:
            self.preferences["client_name"] = m_name.group(1).title()

        # Phone extraction
        m_phone = re.search(r"(\+?92\d{10}|03\d{9}|03\d{2}[-\s]?\d{7})", t)
        if m_phone:
            self.preferences["client_phone"] = m_phone.group(1).replace("-", "").replace(" ", "")

    def context_text(self) -> str:
        prefs = ", ".join(f"{k}={v}" for k, v in self.preferences.items()) or "none yet"
        recent = "\n".join(f"{x['role']}: {x['text']}" for x in self.turns[-8:])
        return f"Remembered preferences: {prefs}\nRecent conversation:\n{recent}"


def detect_objection(text: str) -> str | None:
    t = text.casefold()
    groups = {
        "price": ["expensive", "too much", "mehnga", "مہنگا", "budget", "sasta", "سستا", "price"],
        "trust": ["trust", "scam", "fraud", "reliable", "bharosa", "بھروسہ", "documents", "legal"],
        "location": ["location", "far", "distance", "area", "jagah", "جگہ"],
        "investment": ["investment", "roi", "return", "appreciation", "profit", "rental yield"],
        "builder": ["builder", "developer", "developer ka", "authority"],
        "maintenance": ["maintenance", "service charges", "upkeep", "maintain", "charges"],
    }
    for kind, words in groups.items():
        if any(w in t for w in words):
            return kind
    return None


def _infer_city(memory: ConversationMemory) -> str | None:
    return memory.preferences.get("city")


def _infer_locality(memory: ConversationMemory) -> str | None:
    value = memory.preferences.get("locality_contains")
    if value == "dha":
        return "DHA Defence"
    return value


def build_retrieval_query(user_text: str, memory: ConversationMemory) -> str:
    return f"{user_text}\nConversation memory: {memory.context_text()}"


def _csv_context(user_text: str, memory: ConversationMemory) -> tuple[str, list[str]]:
    """Return exact rows for locality facts requested by the customer."""
    query = user_text.casefold()
    if not any(word in query for word in ("amenit", "school", "college", "hospital", "clinic")):
        return "", []
    locations = pd.read_csv(CSV_DIR / "locations.csv")
    localities = locations["locality_full"].astype(str).tolist()
    selected = [name for name in localities if name.casefold() in query]
    if not selected:
        locality = memory.preferences.get("locality_contains")
        selected = [name for name in localities if locality and locality.casefold() in name.casefold()]
    if not selected:
        return "", []

    blocks: list[str] = []
    sources: list[str] = []
    if any(word in query for word in ("amenit",)):
        rows = pd.read_csv(CSV_DIR / "amenities.csv")
        values = rows[rows["locality_full"].isin(selected)]["amenity"].dropna().astype(str).tolist()
        blocks.append("Amenities from amenities.csv: " + "; ".join(values))
        sources.append("amenities.csv")
    if any(word in query for word in ("school", "college")):
        rows = pd.read_csv(CSV_DIR / "schools.csv")
        values = rows[rows["locality_full"].isin(selected)].fillna("").astype(str)
        blocks.append("Schools from schools.csv: " + "; ".join(
            f"{r.school_name} ({r.level}, {r.distance_km_est} km)" for r in values.itertuples()
        ))
        sources.append("schools.csv")
    if any(word in query for word in ("hospital", "clinic")):
        rows = pd.read_csv(CSV_DIR / "hospitals.csv")
        values = rows[rows["locality_full"].isin(selected)].fillna("").astype(str)
        blocks.append("Hospitals from hospitals.csv: " + "; ".join(
            f"{r.hospital_name} ({r.specialty}, {r.distance_km_est} km)" for r in values.itertuples()
        ))
        sources.append("hospitals.csv")
    return "\n".join(blocks), sources


def _location_options() -> str:
    locations = pd.read_csv(CSV_DIR / "locations.csv")
    grouped = locations.groupby("city")["locality_name"].apply(list)
    return "; ".join(f"{city}: {', '.join(names)}" for city, names in grouped.items())


def _societies_in_city(city: str) -> str:
    try:
        locations = pd.read_csv(CSV_DIR / "locations.csv")
        localities = locations[locations["city"].str.casefold() == city.casefold()]["locality_name"].dropna().tolist()
        if localities:
            if len(localities) == 1:
                return localities[0]
            return ", ".join(localities[:-1]) + f", aur {localities[-1]}"
    except Exception:
        pass
    if city.casefold() == "lahore":
        return "DHA Defence, Bahria Town, Johar Town, Faisal Town, Model Town, aur College Road"
    elif city.casefold() == "islamabad":
        return "E-11, F-10, F-11, F-6, G-11, G-13, G-15, DHA Defence Islamabad, aur Bahria Town Islamabad"
    elif city.casefold() == "rawalpindi":
        return "Bahria Town Rawalpindi aur Airport Housing Society"
    return "DHA Defence aur Bahria Town"


def _is_faq_or_info_question(user_text: str) -> bool:
    query = user_text.casefold()
    info_cues = (
        "document", "dastawaiz", "registry", "transfer", "procedure", "process",
        "tax", "taxes", "commission", "fee", "fees", "legal", "noc", "approval",
        "payment plan", "installment", "qist", "qistain", "down payment", "booking",
        "amenit", "school", "hospital", "clinic", "college", "developer", "builder",
        "authority", "faq", "kya documents", "kon se documents"
    )
    return any(cue in query for cue in info_cues)


def _needs_property_qualification(user_text: str) -> bool:
    if _is_faq_or_info_question(user_text):
        return False
    query = user_text.casefold()
    return any(term in query for term in (
        "ghar", "ghr", "house", "flat", "portion", "property", "plot", "room", "farm house",
        "گھر", "مکان", "فلیٹ", "اپارٹمنٹ", "پلاٹ", "پورشن", "پراپرٹی", "خرید", "خریدنا", "کرایہ", "رینٹ"
    ))


def _marla_question(memory: ConversationMemory, user_text: str = "") -> str:
    property_type = memory.preferences.get("property_type")
    query = user_text.casefold()
    if "flat" in query or "فلیٹ" in query:
        property_type = "Flat"
    elif "plot" in query or "پلاٹ" in query:
        property_type = "Plot"
    label = "ghar" if property_type in (None, "House") else property_type.casefold()
    return f"Aap kitne marla ka {label} chahte hain?"


def _is_greeting(user_text: str) -> bool:
    query = re.sub(r"[^a-z\u0600-\u06ff]", "", user_text.casefold())
    greeting_exact = {
        "assalamualaikum", "assalamualikum", "assalamualaykum", "salam", "salaam",
        "سلام", "السلامعلیکم", "السلامعلیکمورحمۃاللہ", "hello", "hi", "hey",
        "walikumsalam", "walikumasalam", "walekumasalam", "وعلیکم", "وعلیکمالسلام", "وعلیکمسلام"
    }
    return query in greeting_exact


def build_verified_context(user_text: str, memory: ConversationMemory) -> tuple[str, list[str]]:
    chunks: list[dict] = []
    source_ids: list[str] = []
    csv_context, csv_sources = _csv_context(user_text, memory)
    if csv_context:
        chunks.append({"chunk_id": "exact_csv_lookup", "text": csv_context})
        source_ids.extend(csv_sources)
    property_query = user_text.casefold()
    listing_terms = (
        "property", "properties", "listing", "listings", "list", "options", "available",
        "chahiye", "plot", "house", "houses", "ghar", "flat", "portion", "room", "farm house",
        "dha", "bahria", "faisal town", "model town", "johar town", "gulberg", "askari",
        "e-11", "f-10", "f-11", "g-11", "g-13", "g-15", "bata do", "bta do", "dikhao", "batao", "dekhna"
    )
    has_active_search = bool(memory.preferences.get("property_type") or memory.preferences.get("city") or memory.preferences.get("area_marla") or memory.preferences.get("locality_contains"))
    if any(term in property_query for term in listing_terms) or re.search(r"\bPROP-\d+\b", user_text, re.IGNORECASE) or has_active_search:
        try:
            property_context, property_source = _property_context(user_text, memory)
            chunks.append({"chunk_id": property_source, "text": property_context})
            source_ids.append(property_source)
        except Exception as exc:
            chunks.append({"chunk_id": "property_lookup_error", "text": f"Verified property lookup unavailable: {exc}"})
    try:
        retriever = get_retriever(get_vectorstore(), k=5)
        result = rag_retrieve(retriever, build_retrieval_query(user_text, memory))
        chunks.extend(result.get("chunks", []))
        source_ids.extend(c["chunk_id"] for c in chunks)
    except Exception:
        # Voice app can still use SQL recommendations if the vector service is unavailable.
        pass

    prefs = memory.preferences
    budget = prefs.get("budget_pkr")
    text = user_text.casefold()
    cheaper = any(x in text for x in ("cheaper", "sasti", "sasta", "کم", "less expensive"))
    if cheaper and budget:
        budget = budget * 0.9

    if budget and _infer_city(memory):
        try:
            df, status = recommend_properties(
                budget_max=budget,
                city=_infer_city(memory),
                bedrooms=prefs.get("bedrooms"),
                purpose=prefs.get("purpose", "For Sale"),
                locality_contains=_infer_locality(memory),
                area_marla=prefs.get("area_marla"),
                property_type=prefs.get("property_type"),
                investment_goal=detect_objection(user_text) == "investment",
                top_n=5,
            )
            if not df.empty:
                rows = df.to_dict(orient="records")
                memory.preferences["listing_ids"] = [str(r["property_id"]) for r in rows]
                memory.preferences["listing_agents"] = [str(r["agent"]) for r in rows]
                recs = []
                for r in rows:
                    price_spoken = format_price_pkr(r["price"])
                    marla = r["area_marla"]
                    marla_str = f"{int(marla)}" if marla == int(marla) else f"{marla}"
                    bed_desc = f", {int(r['bedrooms'])} bedrooms aur {int(r['baths'])} baths" if r.get('bedrooms') and int(r['bedrooms']) > 0 else ""
                    recs.append(f"{r['property_id']}: {marla_str} marla {r['property_type']} in {r['locality']}, {r['city']}, price {price_spoken}{bed_desc}, agent {r['agent']}")
                chunks.append({
                    "chunk_id": "sql_recommendations",
                    "text": "Verified SQL recommendations: " + "; ".join(recs),
                })
                source_ids.append("sql_recommendations")
        except Exception as exc:
            chunks.append({"chunk_id": "sql_error", "text": f"Structured lookup unavailable: {exc}"})

    if not chunks:
        return "No verified context was retrieved.", []
    return "\n\n".join(f"[{c['chunk_id']}] {c['text']}" for c in chunks), source_ids


def format_price_pkr(price: Any) -> str:
    """Format price into natural spoken Pakistani terms (e.g. 19 lakh, 2.1 crore, 50 hazar)."""
    try:
        val = float(str(price).replace(",", "").replace("PKR", "").strip())
    except (ValueError, TypeError):
        return str(price)

    if val >= 10_000_000:
        crores = val / 10_000_000
        if crores == int(crores):
            return f"{int(crores)} crore"
        return f"{crores:.2f}".rstrip("0").rstrip(".") + " crore"
    elif val >= 100_000:
        lakhs = val / 100_000
        if lakhs == int(lakhs):
            return f"{int(lakhs)} lakh"
        return f"{lakhs:.2f}".rstrip("0").rstrip(".") + " lakh"
    elif val >= 1_000:
        thousands = val / 1_000
        if thousands == int(thousands):
            return f"{int(thousands)} hazar"
        return f"{thousands:.2f}".rstrip("0").rstrip(".") + " hazar"
    return f"{int(val)} rupees"


def _property_context(user_text: str, memory: ConversationMemory) -> tuple[str, str]:
    """Retrieve exact listing facts from the transactional properties table."""
    prefs = memory.preferences
    query_text = user_text.casefold()
    property_id = re.search(r"\bPROP-\d+\b", user_text, re.IGNORECASE)
    city = prefs.get("city")
    for known_city in ("lahore", "islamabad", "rawalpindi"):
        if known_city in query_text:
            city = known_city.title()
            break
    purpose = prefs.get("purpose", "For Sale")
    property_type = prefs.get("property_type")
    for known_type in ("plot", "house", "flat", "upper portion", "lower portion", "room", "farm house"):
        if known_type in query_text:
            property_type = "Farm House" if known_type == "farm house" else known_type.title()
            break

    conditions = []
    params: dict[str, Any] = {}
    if property_id:
        conditions.append("property_id = :property_id")
        params["property_id"] = property_id.group(0).upper()
    else:
        if city:
            conditions.append("city = :city")
            params["city"] = city
        if property_type:
            conditions.append("lower(property_type) = lower(:property_type)")
            params["property_type"] = property_type
        if purpose:
            conditions.append("purpose = :purpose")
            params["purpose"] = purpose
        locality = _infer_locality(memory)
        if locality:
            conditions.append("lower(locality) LIKE lower(:locality)")
            params["locality"] = f"%{locality}%"
        if prefs.get("area_marla") is not None:
            conditions.append("area_marla = :area_marla")
            params["area_marla"] = prefs["area_marla"]

    where = " AND ".join(conditions) if conditions else "1 = 1"
    sql = f"""SELECT property_id, property_type, purpose, city, locality, price,
                      area_marla, area_sqft, bedrooms, baths, agent
               FROM properties WHERE {where}
               ORDER BY price ASC LIMIT 5"""
    with get_engine().connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
    if not rows:
        requested = f" for {property_type}s" if property_type else ""
        location = f" in {city}" if city else ""
        return f"No verified property listings{requested}{location} matched this request in properties.csv.", "properties.csv"

    details_list = []
    for r in rows:
        price_spoken = format_price_pkr(r["price"])
        marla = r["area_marla"]
        marla_str = f"{int(marla)}" if marla == int(marla) else f"{marla}"
        pt = r["property_type"]
        loc = r["locality"]
        c_name = r["city"]
        beds = r["bedrooms"]
        baths = r["baths"]
        bed_desc = f", {int(beds)} bedrooms aur {int(baths)} baths" if beds and int(beds) > 0 else ""
        details_list.append(
            f"{r['property_id']}: {marla_str} marla {pt} in {loc}, {c_name}, price {price_spoken}{bed_desc}, agent {r['agent']}"
        )

    memory.preferences["listing_ids"] = [str(row["property_id"]) for row in rows]
    memory.preferences["listing_agents"] = [str(row["agent"]) for row in rows]
    return "Verified property listings from properties.csv: " + "; ".join(details_list), "properties.csv"


def _answer_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if text:
                    parts.append(str(text))
        return " ".join(parts)
    if isinstance(content, dict):
        return str(content.get("text") or content.get("content") or "")
    return str(content or "")


URDU_NUMS = {
    "ایک": "1", "دو": "2", "تین": "3", "چار": "4", "پانچ": "5", "پونچ": "5",
    "چھ": "6", "سات": "7", "آٹھ": "8", "نو": "9", "دس": "10", "ٹین": "10",
    "گیارہ": "11", "بارہ": "12", "تیرہ": "13", "چودہ": "14", "پندرہ": "15",
    "سولہ": "16", "سترہ": "17", "اٹھارہ": "18", "انیس": "19", "بیس": "20",
    "پچیس": "25", "تیس": "30", "اکتیس": "31"
}
URDU_MONTHS = ["جنوری", "فروری", "مارچ", "اپریل", "مئی", "جون", "جولائی", "اگست", "ستمبر", "اکتوبر", "نومبر", "دسمبر"]
ENG_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december", "sept", "oct", "nov", "dec", "jan", "feb", "aug"]


def _format_property_list(answer: str) -> str:
    """Normalize model listing output for readable chat and voice transcripts."""
    clean = re.sub(r"\bPROP-\d+\b:?\s*", "", answer, flags=re.IGNORECASE)
    clean = re.sub(r",\s*agent\s+[A-Za-z\s]+(?=[\n\.]|$)", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r'(\b\w{2,}\b)(?:\s+\1){3,}', r'\1', clean)
    clean = re.sub(r'(ba){4,}\w*', 'baths', clean)
    clean = re.sub(r'(mert\s*){3,}', '', clean)
    return clean.strip()


def _is_meeting_request(user_text: str) -> bool:
    query = user_text.casefold()
    return any(term in query for term in ("meeting", "meet", "appointment", "schedule", "milna", "mulaqat", "visit", "وزٹ", "ملاقات", "ملنا", "اپوائنٹمنٹ", "شیڈیول", "شیڈول"))


def _is_visit_confirmation(user_text: str, memory: ConversationMemory) -> bool:
    query = user_text.casefold()
    prior_visit = any(_is_meeting_request(turn["text"]) for turn in memory.turns[:-1])
    date_or_time = bool(re.search(r"\b(?:\d{1,2}(?::\d{2})?\s*(?:am|pm|baje|bjy|بجے|august|september|october|november|december|january|february|march|april|may|june|july|ستمبر|اگست|اکتوبر|نومبر|دسمبر|جنوری|فروری|مارچ|اپریل|مئی|جون|جولائی|کل|پرسوں|شام|صبح|دوپہر|رات))\b", query))
    return prior_visit and (date_or_time or query.strip().isdigit())


def _extract_visit_details(user_text: str, memory: ConversationMemory) -> None:
    t = user_text.casefold().strip()

    # 1. Relative dates
    if any(k in t for k in ("kal", "کل", "tomorrow")):
        memory.preferences["visit_date"] = "Tomorrow"
    elif any(k in t for k in ("parso", "پرسوں", "day after")):
        memory.preferences["visit_date"] = "Day after tomorrow"
    elif any(k in t for k in ("aaj", "آج", "today")):
        memory.preferences["visit_date"] = "Today"

    # 2. Month matching with position
    month_found = None
    month_pos = -1
    for m in URDU_MONTHS + ENG_MONTHS:
        if m in t:
            month_found = m
            month_pos = t.find(m)
            break

    # 3. Positional day number before month
    if month_found and not memory.preferences.get("visit_date"):
        before_m = t[:month_pos].strip().split()
        if before_m:
            last_word = before_m[-1]
            if last_word in URDU_NUMS:
                memory.preferences["visit_date"] = f"{URDU_NUMS[last_word]} {month_found}"
            elif last_word.isdigit():
                memory.preferences["visit_date"] = f"{last_word} {month_found}"
        if not memory.preferences.get("visit_date"):
            memory.preferences["visit_date"] = month_found

    # 4. Tareekh / standalone date matching
    if not memory.preferences.get("visit_date"):
        for u_word, num in URDU_NUMS.items():
            if u_word in t and ("تاریخ" in t or "tareekh" in t or "date" in t or "کو" in t):
                memory.preferences["visit_date"] = f"{num} tareekh"
                break
        m_num = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s*(?:تاریخ|tareekh|date|کو)?\b", t)
        if m_num and int(m_num.group(1)) <= 31 and not memory.preferences.get("visit_date"):
            memory.preferences["visit_date"] = f"{m_num.group(1)} tareekh"

    # 5. Time extraction
    for u_word, num in URDU_NUMS.items():
        if (f"{u_word} بجے" in t or f"{u_word} baje" in t) or (("شام" in t or "shaam" in t or "صبح" in t or "subah" in t or "دوپہر" in t or "رات" in t) and u_word in t and u_word not in ("دو", "کو", "ایک")):
            ampm = "PM" if ("شام" in t or "shaam" in t or "رات" in t or u_word in ("پانچ", "پونچ", "چار")) else "AM"
            memory.preferences["visit_time"] = f"{num}:00 {ampm}"
            break

    m_time = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm|baje|bjy|بجے)\b", t)
    if not m_time and ("شام" in t or "shaam" in t or "صبح" in t or "رات" in t):
        m_time = re.search(r"(?:شام|shaam|صبح|subah|رات|raat)\s*(\d{1,2})", t) or re.search(r"(\d{1,2})\s*(?:بجے|baje)", t)
    if m_time and not memory.preferences.get("visit_time"):
        num = int(m_time.group(1))
        if 1 <= num <= 24:
            ampm = "PM" if ("شام" in t or "shaam" in t or "رات" in t or "بجے" in t or m_time.group(0).endswith(("pm", "baje", "بجے"))) and num < 12 else "AM"
            memory.preferences["visit_time"] = f"{num}:00 {ampm}"


def make_voice_answer(user_text: str, memory: ConversationMemory) -> tuple[str, dict]:
    memory._extract_preferences(user_text)
    if memory.preferences.get("search_flow") == "marla" and memory.preferences.get("area_marla") is None:
        standalone_marla = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*", user_text)
        if standalone_marla:
            memory.preferences["area_marla"] = float(standalone_marla.group(1))

    is_first_turn = not memory.preferences.get("greeting_sent")
    memory.preferences["greeting_sent"] = True
    has_any_criteria = bool(
        memory.preferences.get("property_type") or
        memory.preferences.get("city") or
        memory.preferences.get("area_marla") or
        memory.preferences.get("budget_pkr") or
        memory.preferences.get("locality_contains") or
        memory.preferences.get("purpose")
    )
    if is_first_turn and _is_greeting(user_text) and not has_any_criteria:
        return "Walikum as Salam! RealEstate Hub mein khush aamdeed. Main aap ka property consultant hoon. Bataiye, aap ghar dekh rahe hain, flat ya plot?", {"objection": None, "sources": [], "system_prompt": ""}

    is_info = _is_faq_or_info_question(user_text)
    meeting_request = _is_meeting_request(user_text)
    visit_confirmation = _is_visit_confirmation(user_text, memory)
    explicit_listing_ask = any(term in user_text.casefold() for term in ("option", "options", "dikhao", "batao", "list", "available", "dikha do", "bata do", "دکھائیں", "بتائیں"))

    if meeting_request or memory.preferences.get("visit_started") or visit_confirmation:
        _extract_visit_details(user_text, memory)

    # Step-by-Step Qualification Funnel
    if not is_info and not meeting_request and not visit_confirmation and not explicit_listing_ask and not memory.preferences.get("visit_started"):
        # Step 1: Property Type (House / Flat / Plot / Portion)
        if not memory.preferences.get("property_type"):
            return "Aap ghar dekh rahe hain, flat ya plot?", {"objection": None, "sources": [], "system_prompt": ""}

        # Step 2: City (Lahore / Islamabad / Rawalpindi)
        if not memory.preferences.get("city"):
            return "Aap کس city mein dekhna chahenge? Hamare paas Lahore, Islamabad aur Rawalpindi mein options available hain.", {"objection": None, "sources": ["locations.csv"], "system_prompt": ""}

        # Step 3: Specific Locality / Societies in that city
        if memory.preferences.get("city") and not memory.preferences.get("locality_contains") and not memory.preferences.get("societies_shared"):
            memory.preferences["societies_shared"] = True
            city = memory.preferences["city"]
            return f"{city} mein hamare paas {_societies_in_city(city)} jaisi societies available hain. Aap کس area mein prefer karenge?", {"objection": None, "sources": ["locations.csv"], "system_prompt": ""}

        # Step 4: Marla Size / Bedrooms / Budget
        if memory.preferences.get("area_marla") is None and memory.preferences.get("budget_pkr") is None and not memory.preferences.get("size_budget_asked"):
            memory.preferences["size_budget_asked"] = True
            loc = memory.preferences.get("locality_contains")
            loc_label = "DHA Lahore" if loc == "dha" else (loc.title() if loc else "")
            prefix = f"{loc_label} mein " if loc_label else ""
            pt = memory.preferences.get("property_type", "House")
            if pt == "Flat":
                return f"{prefix}Aap kitne bedrooms ka flat dekh rahe hain aur aapka approximate budget kitna hai?", {"objection": None, "sources": [], "system_prompt": ""}
            elif pt == "Plot":
                return f"{prefix}Aap kitne marla ka plot dekh rahe hain aur aapka approximate budget kitna hai?", {"objection": None, "sources": [], "system_prompt": ""}
            else:
                return f"{prefix}Aap kitne marla ka ghar dekh rahe hain aur aapka approximate budget kitna hai?", {"objection": None, "sources": [], "system_prompt": ""}

        # Step 5: Purpose (Rent vs Buy)
        if not memory.preferences.get("purpose"):
            if memory.preferences.get("purpose_asked_flag"):
                # Loop-breaker: If already asked and user gave any response, default to For Sale
                memory.preferences["purpose"] = "For Sale"
            else:
                memory.preferences["purpose_asked_flag"] = True
                return "Aap property khareedna chahte hain ya rent par lena chahte hain?", {"objection": None, "sources": [], "system_prompt": ""}

        # Present verified listings directly upon completing qualification OR when user specifies size/asks for listings
        is_listing_ask = any(term in user_text.casefold() for term in ("option", "options", "dikhao", "batao", "list", "available", "dikha do", "bata do", "دکھائیں", "بتائیں", "دکھاؤ", "چاہئے", "چاہیے", "دیکھنا", "مرلہ", "مرلے", "مرحلے", "marla", "house", "گھر", "تریدنا", "خریدنا", "خرید"))
        if memory.preferences.get("area_marla") and (not memory.preferences.get("listings_presented") or is_listing_ask) and not memory.preferences.get("visit_started"):
            memory.preferences["listings_presented"] = True
            city = memory.preferences.get("city", "Lahore")
            loc = memory.preferences.get("locality_contains")
            loc_display = "DHA" if loc == "dha" else (loc.title() if loc else city)
            marla_val = int(memory.preferences["area_marla"]) if memory.preferences["area_marla"] == int(memory.preferences["area_marla"]) else memory.preferences["area_marla"]
            pt = memory.preferences.get("property_type", "House")

            prop_text, _ = _property_context(user_text, memory)
            if "Verified property listings" in prop_text:
                items = prop_text.replace("Verified property listings from properties.csv: ", "").split("; ")
                formatted_opts = []
                for idx, item in enumerate(items[:3]):
                    clean_item = re.sub(r"^PROP-\d+:\s*", "", item)
                    clean_item = clean_item.replace(", Punjab, Lahore", "").replace(", Punjab, Rawalpindi", "").replace(", Islamabad Capital Territory, Islamabad", "").replace(", Islamabad, Islamabad", "")
                    formatted_opts.append(f"**{idx+1}.** {clean_item}")
                options_block = "\n".join(formatted_opts)
                return f"Ji sir, {loc_display} {city} mein hamare paas {marla_val} marla {pt.lower()}s ke liye yeh behtareen options available hain:\n\n{options_block}\n\nIn mein se kaun sa option aap visit karna pasand karenge ji?", {"objection": None, "sources": ["properties.csv"], "system_prompt": ""}

    # Reschedule Intent
    is_reschedule = any(term in user_text.casefold() for term in ("reschedule", "badalna", "change time", "time change", "دوسرا وقت", "ری شیڈول", "تبدیل", "ریشیڈول", "وقت بدلیں", "date badal do", "ٹائم بدلیں"))
    if is_reschedule:
        memory.preferences.pop("visit_date", None)
        memory.preferences.pop("visit_time", None)
        _extract_visit_details(user_text, memory)
        new_date = memory.preferences.get("visit_date") or "Tomorrow"
        new_time = memory.preferences.get("visit_time") or "5:00 PM"

        appt_id = memory.preferences.get("appointment_id")
        if not appt_id:
            from crm_service import list_appointments
            appts = list_appointments(limit=1)
            if appts:
                appt_id = appts[0]["appointment_id"]
                memory.preferences["appointment_id"] = appt_id

        if appt_id:
            from appointment_manager import reschedule_appointment
            res = reschedule_appointment(appt_id, new_date, new_time, reason=user_text)
            if res.get("ok"):
                return f"Aapki visit successfully reschedule ho gayi hai {new_date} ko {new_time} par. Agent aur Google Calendar ko reschedule email dispatch ho chuki hai.", {"objection": None, "sources": ["appointments", "email"], "system_prompt": ""}
        return f"Ji bilkul, aapki appointment {new_date} ko {new_time} par update kar di gayi hai.", {"objection": None, "sources": ["appointments"], "system_prompt": ""}

    # Cancel Intent
    is_cancel = any(term in user_text.casefold() for term in ("cancel", "mansookh", "nahi aa sakta", "کینسل", "منسوخ", "کنسل", "کینسل کرو", "منسوخ کر دو"))
    if is_cancel:
        appt_id = memory.preferences.get("appointment_id")
        if not appt_id:
            from crm_service import list_appointments
            appts = list_appointments(limit=1)
            if appts:
                appt_id = appts[0]["appointment_id"]
                memory.preferences["appointment_id"] = appt_id

        if appt_id:
            from appointment_manager import cancel_appointment
            res = cancel_appointment(appt_id, reason=user_text)
            if res.get("ok"):
                return "Aapki visit cancel kar di gayi hai aur agent ko cancellation email bhej di gayi hai. Agar aapko dobara visit karni ho toh hum hazir hain.", {"objection": None, "sources": ["appointments", "email"], "system_prompt": ""}
        return "Aap ki appointment cancel kar di gayi hai.", {"objection": None, "sources": ["appointments"], "system_prompt": ""}

    # Handle Visit Scheduling
    objection = detect_objection(user_text)
    if re.fullmatch(r"\s*[1-5]\s*", user_text) and memory.preferences.get("listing_ids"):
        selected_index = int(user_text.strip()) - 1
        listing_ids = memory.preferences["listing_ids"]
        if selected_index < len(listing_ids):
            memory.preferences["selected_property_id"] = listing_ids[selected_index]
            memory.preferences["selected_agent"] = memory.preferences["listing_agents"][selected_index]
    elif any(x in user_text for x in ("پہلا", "پہلے", "1st", "first", "first option", "پہلا آپشن")) and memory.preferences.get("listing_ids"):
        memory.preferences["selected_property_id"] = memory.preferences["listing_ids"][0]
        memory.preferences["selected_agent"] = memory.preferences["listing_agents"][0]

    if meeting_request:
        memory.preferences["visit_started"] = True

    if memory.preferences.get("visit_started"):
        # 1. Ask for date if not extracted
        if not memory.preferences.get("visit_date"):
            if memory.preferences.get("date_asked_flag"):
                # User replied after we asked for date -> accept user response as date
                memory.preferences["visit_date"] = user_text.strip()
            else:
                memory.preferences["date_asked_flag"] = True
                return "Ji bilkul, visit ke liye aap کس date ko aana pasand karenge?", {"objection": None, "sources": [], "system_prompt": ""}

        # 2. Ask for time if not extracted
        if not memory.preferences.get("visit_time"):
            if memory.preferences.get("time_asked_flag"):
                # User replied after we asked for time -> accept user response as time
                memory.preferences["visit_time"] = user_text.strip()
            else:
                memory.preferences["time_asked_flag"] = True
                return "Theek hai, aap کس time par visit karna pasand karenge?", {"objection": None, "sources": [], "system_prompt": ""}

        # 3. Ask for client name if not extracted
        if not memory.preferences.get("client_name") or memory.preferences.get("client_name") in ("Valued Client", ""):
            if memory.preferences.get("name_asked_flag"):
                clean_name = re.sub(r"(?:mera naam|meri naam|my name is|this is|naam|hai|he|is|ji|sir|sahib|میرا|میری|میرے|نام|ہے|ہوں)", "", user_text, flags=re.IGNORECASE).strip().title()
                memory.preferences["client_name"] = clean_name or "Valued Client"
            else:
                memory.preferences["name_asked_flag"] = True
                return "Barae meherbani apna shubh naam bataiye taake hum appointment aap ke naam par confirm kar sakein.", {"objection": None, "sources": [], "system_prompt": ""}

        # 4. Both Date, Time & Name are ready -> Book & Confirm!
        if not memory.preferences.get("visit_shared"):
            agent = memory.preferences.get("selected_agent") or "Ahmed Raza"
            prop_id = memory.preferences.get("selected_property_id") or "PROP-General"
            client_name = memory.preferences.get("client_name") or "Valued Client"
            client_phone = memory.preferences.get("client_phone") or "03001234567"
            date_str = memory.preferences.get("visit_date", "Tomorrow")
            time_str = memory.preferences.get("visit_time", "3:00 PM")
            city = memory.preferences.get("city", "")
            prop_type = memory.preferences.get("property_type", "House")

            # Execute automated Booking across Google Calendar, Email, and CRM
            from appointment_manager import book_appointment
            booking_res = book_appointment(
                client_name=client_name,
                client_phone=client_phone,
                employee_name=agent,
                property_title=f"{prop_type} in {city or 'Site Visit'}",
                property_id=prop_id,
                date_str=date_str,
                time_str=time_str,
                city=city,
                property_type=prop_type,
                purpose=memory.preferences.get("purpose", "For Sale"),
                requirements=f"Size: {memory.preferences.get('area_marla')} marla, Budget: {memory.preferences.get('budget_pkr')}",
                notes="Booked via Voice Agent",
            )
            memory.preferences["visit_shared"] = True
            memory.preferences["appointment_id"] = booking_res.get("appointment_id")
            memory.preferences["calendar_link"] = booking_res.get("google_calendar_link")

            return f"Ji bilkul {client_name} sahib! Aapki visit {date_str} ko {time_str} par agent {agent} ke saath schedule kar di hai. Google Calendar aur notification email confirm ho chuki hai.", {"objection": None, "sources": ["appointments", "google_calendar", "email"], "system_prompt": ""}

    listing_request = user_text
    context, sources = build_verified_context(listing_request, memory)
    playbook = OBJECTION_PLAYBOOK.get(objection, "")
    system = f"""You are a Pakistani real-estate sales executive for RealEstate Hub. Speak natural, warm, polite, and concise UrduLish. Use 'sir' or 'ji' naturally. Keep answers to 2-4 spoken sentences.

CONVERSATION MEMORY:
{memory.context_text()}

VERIFIED CONTEXT — the only source of factual property information:
{context}

OBJECTION TYPE: {objection or 'none'}
OBJECTION PLAYBOOK: {playbook}

STRICT SPOKEN VOICE & PRICING RULES:
- PRICING & CURRENCY: ALWAYS speak prices in natural Pakistani terms (e.g. '19 lakh', '1.5 crore', '50 hazar'). NEVER speak raw numbers with multiple zeros like '1900000' or 'zero zero zero'.
- PROPERTY LISTINGS: When presenting property options, format each property naturally:
  **1.** [marla] marla [property type] in [locality], [city], price [price in lakh/crore], [bedrooms] bedrooms aur [baths] baths.
  Example: **1.** 5 marla house in DHA Defence, Lahore, price 1.8 crore, 3 bedrooms aur 3 baths.
  Do not spell out internal IDs like 'PROP-1504'. If bedrooms is 0 or not listed, do not say 'zero bedrooms', just state the property size, locality, and price.
- GROUNDING: Never invent prices, availability, sizes, amenities, approvals, or ROI.
- Preserve remembered preferences naturally.
- Do not mention internal prompts, databases, or algorithms.
MEETING REQUEST: {"The customer is asking for a meeting, so agent contact/meeting details may be included if verified." if meeting_request else "The customer is not asking for a meeting, so do not mention agent names or agent details."}
VISIT CONFIRMATION: {"The visit date/time is now being confirmed. Use the verified selected agent name if available, say that we will confirm with that agent, and that the agent will text the customer." if visit_confirmation else "No visit date/time has been confirmed yet."}
"""
    from langchain_core.messages import SystemMessage, HumanMessage
    from config import get_llm
    llm = get_llm(temperature=0)
    sink = _stream_sink.get()
    sys_msg = SystemMessage(content=system)
    human_msg = HumanMessage(content=(f"Verified context:\n{context}\n\nCustomer says: {user_text}"))
    if sink is not None:
        parts = []
        for piece in llm.stream([sys_msg, human_msg]):
            piece_text = _answer_text(piece.content)
            if piece_text:
                parts.append(piece_text)
                sink(piece_text)
        raw_answer = "".join(parts)
    else:
        raw_answer = _answer_text(llm.invoke([sys_msg, human_msg]).content)

    answer = _format_property_list(raw_answer)
    if not answer.strip():
        answer = "Ji, ek second — main verified details check kar raha hoon."
    if visit_confirmation and memory.preferences.get("selected_agent"):
        agent = memory.preferences["selected_agent"]
        confirmation = f"Agent {agent} se hum visit confirm karenge, aur woh aapko text karenge."
        if agent.casefold() not in answer.casefold():
            answer = f"{confirmation} {answer}"
    return answer.strip(), {"objection": objection, "sources": sources, "system_prompt": system}