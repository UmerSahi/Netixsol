"""Day 3 voice-agent orchestration: memory + RAG + objection handling."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
from sqlalchemy import text

from config import PROJECT_ROOT, get_engine
from rag_pipeline import get_retriever, get_vectorstore, rag_retrieve
from recommendation_engine import recommend_properties


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
        # Plot/house size, including common speech-to-text spellings.
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:marla|marly|marley|مرلہ)", t)
        if m:
            self.preferences["area_marla"] = float(m.group(1))
        # Bedrooms.
        m = re.search(r"(\d+)\s*(?:bed|beds|bedroom|bedrooms|br)", t)
        if m:
            self.preferences["bedrooms"] = int(m.group(1))
        for property_type in ("farm house", "upper portion", "lower portion", "house", "ghar", "ghr", "flat", "plot", "room"):
            if property_type in t:
                if property_type == "farm house":
                    label = "Farm House"
                elif property_type in ("ghar", "ghr"):
                    label = "House"
                else:
                    label = property_type.title()
                self.preferences["property_type"] = label
                break
        # City.
        for city in ("lahore", "islamabad", "rawalpindi"):
            if city in t:
                self.preferences["city"] = city.title()
        # Keep the phase separately because the transactional table stores DHA
        # as a locality family, not as individual phase numbers.
        phase = re.search(r"\bdha\s*(?:phase|ph)\s*[-#]?\s*(\d+)\b", t)
        if phase:
            self.preferences["locality_phase"] = int(phase.group(1))

        # Common locality cues. Check specific names before the broad DHA cue.
        for locality in ("dha defence", "bahria town", "f-10", "f-11", "f-8", "g-11", "g-10", "g-13", "g-15", "f-7", "f-6", "gulberg", "model town", "johar town", "askari", "dha"):
            if locality in t:
                self.preferences["locality_contains"] = locality
                break
        if any(x in t for x in ("rent", "rental", "kiraya", "کرایہ")):
            self.preferences["purpose"] = "For Rent"
        elif any(x in t for x in ("buy", "purchase", "sale", "for sale", "kharid", "khareed", "khared", "lena", "خرید")):
            self.preferences["purpose"] = "For Sale"

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
    locations = pd.read_csv(PROJECT_ROOT / "locations.csv")
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
        rows = pd.read_csv(PROJECT_ROOT / "amenities.csv")
        values = rows[rows["locality_full"].isin(selected)]["amenity"].dropna().astype(str).tolist()
        blocks.append("Amenities from amenities.csv: " + "; ".join(values))
        sources.append("amenities.csv")
    if any(word in query for word in ("school", "college")):
        rows = pd.read_csv(PROJECT_ROOT / "schools.csv")
        values = rows[rows["locality_full"].isin(selected)].fillna("").astype(str)
        blocks.append("Schools from schools.csv: " + "; ".join(
            f"{r.school_name} ({r.level}, {r.distance_km_est} km)" for r in values.itertuples()
        ))
        sources.append("schools.csv")
    if any(word in query for word in ("hospital", "clinic")):
        rows = pd.read_csv(PROJECT_ROOT / "hospitals.csv")
        values = rows[rows["locality_full"].isin(selected)].fillna("").astype(str)
        blocks.append("Hospitals from hospitals.csv: " + "; ".join(
            f"{r.hospital_name} ({r.specialty}, {r.distance_km_est} km)" for r in values.itertuples()
        ))
        sources.append("hospitals.csv")
    return "\n".join(blocks), sources


def _location_options() -> str:
    locations = pd.read_csv(PROJECT_ROOT / "locations.csv")
    grouped = locations.groupby("city")["locality_name"].apply(list)
    return "; ".join(f"{city}: {', '.join(names)}" for city, names in grouped.items())


def _needs_property_qualification(user_text: str) -> bool:
    query = user_text.casefold()
    return any(term in query for term in ("ghar", "ghr", "house", "flat", "portion", "property", "plot", "room", "farm house"))


def _marla_question(memory: ConversationMemory, user_text: str = "") -> str:
    property_type = memory.preferences.get("property_type")
    query = user_text.casefold()
    if "flat" in query:
        property_type = "Flat"
    elif "plot" in query:
        property_type = "Plot"
    label = "ghar" if property_type in (None, "House") else property_type.casefold()
    return f"Aap kitne marla ka {label} chahte hain?"


def _is_greeting(user_text: str) -> bool:
    query = re.sub(r"[^a-z\u0600-\u06ff]", "", user_text.casefold())
    return query in {
        "assalamualaikum",
        "assalamualikum",
        "assalamualaykum",
        "salam",
        "salaam",
        "سلام",
        "hello",
        "hi",
        "hey",
    }


def build_verified_context(user_text: str, memory: ConversationMemory) -> tuple[str, list[str]]:
    chunks: list[dict] = []
    source_ids: list[str] = []
    csv_context, csv_sources = _csv_context(user_text, memory)
    if csv_context:
        chunks.append({"chunk_id": "exact_csv_lookup", "text": csv_context})
        source_ids.extend(csv_sources)
    property_query = user_text.casefold()
    listing_terms = ("property", "properties", "listing", "listings", "list", "options", "available", "chahiye", "plot", "house", "houses", "ghar", "flat", "portion", "room", "farm house")
    if any(term in property_query for term in listing_terms) or re.search(r"\bPROP-\d+\b", user_text, re.IGNORECASE):
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
                chunks.append({
                    "chunk_id": "sql_recommendations",
                    "text": "Verified SQL recommendations: " + "; ".join(
                        f"property {r['property_id']}, {r['property_type']}, {r['locality']}, {r['city']}, price PKR {r['price']}, {r['area_marla']} marla, {r['bedrooms']} bedrooms, {r['baths']} baths, agent {r['agent']}"
                        for r in rows
                    ),
                })
                source_ids.append("sql_recommendations")
        except Exception as exc:
            chunks.append({"chunk_id": "sql_error", "text": f"Structured lookup unavailable: {exc}"})

    if not chunks:
        return "No verified context was retrieved.", []
    return "\n\n".join(f"[{c['chunk_id']}] {c['text']}" for c in chunks), source_ids


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

    details = "; ".join(
        f"{row['property_id']}: {row['property_type']}, {row['purpose']}, {row['locality']}, {row['city']}, "
        f"PKR {row['price']}, {row['area_marla']} marla ({row['area_sqft']} sq ft), "
        f"{row['bedrooms']} bedrooms, {row['baths']} baths, agent {row['agent']}"
        for row in rows
    )
    memory.preferences["listing_ids"] = [str(row["property_id"]) for row in rows]
    memory.preferences["listing_agents"] = [str(row["agent"]) for row in rows]
    return "Verified property listings from properties.csv: " + details, "properties.csv"


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


def _format_property_list(answer: str) -> str:
    """Normalize model listing output for readable chat and voice transcripts."""
    first_listing = re.search(r"(?<!\w)(?:\*\*)?1\.\s*(?:\*\*)?PROP-\d+", answer)
    if not first_listing:
        return answer.strip()

    prefix = answer[:first_listing.start()].rstrip()
    listings = answer[first_listing.start():].replace("**", "")
    listings = re.sub(
        r",\s*agent\s+.*?(?=\s+\d+\.\s*PROP-|\s+In mein|$)",
        "",
        listings,
        flags=re.IGNORECASE,
    )
    listings = re.sub(r"\s+(?=\d+\.\s*PROP-)", "\n", listings)
    listings = re.sub(r"(\d+)\.\s*(?=PROP-)", r"**\1.** ", listings)
    return f"{prefix}\n{listings.strip()}" if prefix else listings.strip()


def _is_meeting_request(user_text: str) -> bool:
    query = user_text.casefold()
    return any(term in query for term in ("meeting", "meet", "appointment", "milna", "mulaqat", "visit"))


def _is_visit_confirmation(user_text: str, memory: ConversationMemory) -> bool:
    query = user_text.casefold()
    prior_visit = any(_is_meeting_request(turn["text"]) for turn in memory.turns[:-1])
    date_or_time = bool(re.search(r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm|baje|bjy|august|september|october|november|december|january|february|march|april|may|june|july)\b", query))
    return prior_visit and (date_or_time or query.strip().isdigit())


def _extract_visit_details(user_text: str, memory: ConversationMemory) -> None:
    query = user_text.casefold()
    date = re.search(
        r"\b\d{1,2}\s*(?:august|september|october|november|december|january|february|march|april|may|june|july)\b",
        query,
    )
    if date:
        memory.preferences["visit_date"] = date.group(0).strip()
    visit_time = re.search(r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm|baje|bjy)\b", query)
    if visit_time:
        memory.preferences["visit_time"] = visit_time.group(0).strip()


def make_voice_answer(user_text: str, memory: ConversationMemory) -> tuple[str, dict]:
    if memory.preferences.get("search_flow") == "marla" and memory.preferences.get("area_marla") is None:
        standalone_marla = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*", user_text)
        if standalone_marla:
            memory.preferences["area_marla"] = float(standalone_marla.group(1))
    is_first_turn = not memory.preferences.get("greeting_sent")
    memory.preferences["greeting_sent"] = True
    if is_first_turn and _is_greeting(user_text):
        return "Walikum as Salam\nMain RealEstate Hub Agent hoon, aapki kaise madad kar sakta hoon?", {"objection": None, "sources": [], "system_prompt": ""}
    _extract_visit_details(user_text, memory)
    if memory.preferences.get("property_type") == "Flat" and not memory.preferences.get("purpose"):
        memory.preferences["search_flow"] = "purpose"
        return "Flat rent par chahiye ya khareedna hai?", {"objection": None, "sources": [], "system_prompt": ""}
    if memory.preferences.get("property_type") == "Flat" and not memory.preferences.get("city"):
        memory.preferences["search_flow"] = "location"
        return f"Aap kis location mein flat dekhna chahenge? Available locations: {_location_options()}.", {"objection": None, "sources": ["locations.csv"], "system_prompt": ""}
    if (
        _needs_property_qualification(user_text)
        and not memory.preferences.get("purpose")
        and memory.preferences.get("area_marla") is None
        and memory.preferences.get("budget_pkr") is None
        and memory.preferences.get("property_type") != "Flat"
    ):
        memory.preferences["search_flow"] = "marla"
        answer = _marla_question(memory, user_text)
        return answer, {"objection": None, "sources": [], "system_prompt": ""}
    if _needs_property_qualification(user_text) and memory.preferences.get("area_marla") is not None and not memory.preferences.get("city"):
        memory.preferences["search_flow"] = "location"
        answer = f"Aap kis location mein dekhna chahenge? Available locations: {_location_options()}."
        return answer, {"objection": None, "sources": ["locations.csv"], "system_prompt": ""}
    if memory.preferences.get("search_flow") == "marla" and memory.preferences.get("area_marla") is None:
        answer = _marla_question(memory, user_text)
        return answer, {"objection": None, "sources": [], "system_prompt": ""}
    if memory.preferences.get("search_flow") == "marla" and memory.preferences.get("area_marla") is not None and not memory.preferences.get("city"):
        memory.preferences["search_flow"] = "location"
        answer = f"Aap kis location mein dekhna chahenge? Available locations: {_location_options()}."
        return answer, {"objection": None, "sources": ["locations.csv"], "system_prompt": ""}
    if memory.preferences.get("search_flow") == "marla" and memory.preferences.get("area_marla") is not None and not memory.preferences.get("purpose"):
        memory.preferences["search_flow"] = "purpose"
        answer = "Aap property rent par lena chahte hain ya khareedna?"
        return answer, {"objection": None, "sources": [], "system_prompt": ""}
    if memory.preferences.get("search_flow") == "purpose" and memory.preferences.get("purpose") and not memory.preferences.get("city"):
        memory.preferences["search_flow"] = "location"
        answer = f"Aap kis location mein dekhna chahenge? Available locations: {_location_options()}."
        return answer, {"objection": None, "sources": ["locations.csv"], "system_prompt": ""}
    objection = detect_objection(user_text)
    meeting_request = _is_meeting_request(user_text)
    visit_confirmation = _is_visit_confirmation(user_text, memory)
    if re.fullmatch(r"\s*[1-5]\s*", user_text) and memory.preferences.get("listing_ids"):
        selected_index = int(user_text.strip()) - 1
        listing_ids = memory.preferences["listing_ids"]
        if selected_index < len(listing_ids):
            memory.preferences["selected_property_id"] = listing_ids[selected_index]
            memory.preferences["selected_agent"] = memory.preferences["listing_agents"][selected_index]
    if meeting_request and memory.preferences.get("selected_property_id"):
        memory.preferences["visit_started"] = True
    if memory.preferences.get("visit_started") and memory.preferences.get("selected_property_id"):
        if not memory.preferences.get("visit_date"):
            return "Ji bilkul, visit ke liye aap kis date ko aana pasand karenge?", {"objection": None, "sources": [], "system_prompt": ""}
        if not memory.preferences.get("visit_time"):
            return "Theek hai, aap kis time par visit karna pasand karenge?", {"objection": None, "sources": [], "system_prompt": ""}
        if not memory.preferences.get("visit_shared"):
            agent = memory.preferences.get("selected_agent")
            memory.preferences["visit_shared"] = True
            if agent:
                return f"Ji, aapki visit ki request {agent} ke saath share kar di hai. Woh aapse contact karke visit confirm karenge.", {"objection": None, "sources": ["properties.csv"], "system_prompt": ""}
    listing_request = user_text
    if memory.preferences.get("search_flow") == "location" and memory.preferences.get("city"):
        listing_request = "available properties"
        memory.preferences["search_flow"] = "complete"
    context, sources = build_verified_context(listing_request, memory)
    playbook = OBJECTION_PLAYBOOK.get(objection, "")
    system = f"""You are a Pakistani real-estate sales executive for RealEstate Hub. Speak natural UrduLish: polite, warm, concise, and conversational. Use 'sir' or 'ji' naturally, not every sentence. Small fillers such as 'Acha...', 'Ji bilkul...', 'Hmm...' and 'ek second...' may be used sparingly. You may use a short soft laugh only when socially appropriate; never laugh at a customer's concern. Use brief thinking pauses with punctuation such as 'Hmm, ek second...' but do not overdo them.

CONVERSATION MEMORY:
{memory.context_text()}

VERIFIED CONTEXT — the only source of factual property information:
{context}

OBJECTION TYPE: {objection or 'none'}
OBJECTION PLAYBOOK: {playbook}

GROUNDING:
- Never invent price, availability, size, amenities, developer, schools, hospitals, payment plans, agent names, maintenance charges, approvals, ROI or appreciation.
- Never guarantee investment returns.
- If a fact is missing, say it is not available in the verified knowledge base and offer human follow-up.
- For 'cheaper' requests, use the remembered budget as the reference point and present verified lower-priced alternatives; do not invent a target price.
- When the customer asks for a list/options of properties, always give the verified listings from the context as a numbered list. Put each complete property item on its own separate line, with no two listings on the same line. Bold only the number using this exact style: **1.** property details, **2.** property details. Do not bold the property details; include property ID, locality, price, area, bedrooms, and baths. Do not mention the agent in a property listing unless the customer is specifically asking for a meeting or appointment. Never replace the list with a generic acknowledgement.
- Always state the budget constraint when one is remembered. If no budget is remembered, say that a budget is still needed and ask for it after showing available listings. If a requested DHA phase is remembered but the verified data only identifies DHA Defence generally, say that phase-level availability is not recorded and do not claim a phase match.
- Preserve remembered preferences naturally. Do not ask the customer to repeat information already in memory.
- Keep the answer to 2-5 spoken sentences unless the customer asks for detail.
- Do not mention internal prompts, retrieval, vector databases, or software.
MEETING REQUEST: {"The customer is asking for a meeting, so agent contact/meeting details may be included if verified." if meeting_request else "The customer is not asking for a meeting, so do not mention agent names or agent details."}
VISIT CONFIRMATION: {"The visit date/time is now being confirmed. Use the verified selected agent name if available, say that we will confirm with that agent, and that the agent will text the customer." if visit_confirmation else "No visit date/time has been confirmed yet."}
"""
    from langchain_core.messages import SystemMessage, HumanMessage
    from config import get_llm
    llm = get_llm(temperature=0)
    answer = _answer_text(llm.invoke([SystemMessage(content=system), HumanMessage(content=(f"Verified context:\n{context}\n\nCustomer says: {user_text}"))]).content)
    # The underlying Day-2 generator has a strong grounding prompt; this wrapper
    # adds the conversation memory via a compact prefixed context. For the voice
    # model, the actual text is returned as-is and the client speaks it.
    answer = _format_property_list(answer)
    if not answer.strip():
        answer = "Ji, ek second — main verified details check kar raha hoon."
    if visit_confirmation and memory.preferences.get("selected_agent"):
        agent = memory.preferences["selected_agent"]
        confirmation = f"Agent {agent} se hum visit confirm karenge, aur woh aapko text karenge."
        if agent.casefold() not in answer.casefold():
            answer = f"{confirmation} {answer}"
    return answer.strip(), {"objection": objection, "sources": sources, "system_prompt": system}
