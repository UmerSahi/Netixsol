import asyncio
import json
from pathlib import Path

from voice_agent import (
    ConversationMemory,
    build_retrieval_query,
    build_verified_context,
    detect_objection,
    _format_property_list,
    make_voice_answer,
)


def test_memory_budget_and_locality():
    m = ConversationMemory()
    m.add("user", "Budget 3 crore hai. Lahore mein DHA options chahiye.")
    assert m.preferences["budget_pkr"] == 30_000_000
    assert m.preferences["city"] == "Lahore"
    assert m.preferences["locality_contains"] == "dha"


def test_memory_persists_across_turns():
    m = ConversationMemory()
    m.add("user", "Budget 3 crore hai")
    m.add("assistant", "Ji bilkul.")
    m.add("user", "DHA mein kya options hain?")
    assert m.preferences["budget_pkr"] == 30_000_000
    assert m.preferences["locality_contains"] == "dha"
    assert "budget_pkr=30000000.0" in m.context_text()


def test_objections():
    assert detect_objection("Bohat mehnga hai") == "price"
    assert detect_objection("Builder pe trust kaise karun?") == "trust"
    assert detect_objection("Investment ke liye return kya hoga?") == "investment"
    assert detect_objection("Maintenance charges kitne hain?") == "maintenance"


def test_query_contains_memory():
    m = ConversationMemory(); m.add("user", "Budget 3 crore hai, Lahore DHA")
    q = build_retrieval_query("Us se sasti koi option?", m)
    assert "30000000.0" in q and "Us se sasti" in q


def test_phase_listing_request_keeps_phase_and_returns_properties():
    m = ConversationMemory()
    request = "Lahore DHA phase 6 mein houses ki list dein"
    m.add("user", request)
    context, sources = build_verified_context(request, m)
    assert m.preferences["locality_phase"] == 6
    assert "properties.csv" in sources
    assert "Verified property listings" in context
    assert "PROP-" in context


def test_greeting_only_returns_greeting():
    answer, _ = make_voice_answer("Assalam u ALikum", ConversationMemory())
    assert answer == "Walikum as Salam\nMain RealEstate Hub Agent hoon, aapki kaise madad kar sakta hoon?"
    answer, _ = make_voice_answer("salaam", ConversationMemory())
    assert answer == "Walikum as Salam\nMain RealEstate Hub Agent hoon, aapki kaise madad kar sakta hoon?"
    assert "Real Estate Hub" not in answer


def test_property_request_asks_for_marla_before_other_filters():
    memory = ConversationMemory()
    answer, _ = make_voice_answer("Lahore mein ghar chahiye", memory)
    assert answer == "Aap kitne marla ka ghar chahte hain?"
    assert memory.preferences["search_flow"] == "marla"


def test_memory_extracts_marla():
    memory = ConversationMemory()
    memory.add("user", "5 marly ka ghar chahiye")
    assert memory.preferences["area_marla"] == 5.0


def test_property_formatting_instruction_bolds_only_numbers():
    memory = ConversationMemory()
    memory.add("user", "Budget 3 crore hai, Lahore mein properties buy karni hain")
    _, meta = make_voice_answer("Lahore mein properties ki list chahiye", memory)
    assert "no two listings on the same line" in meta["system_prompt"]
    assert "**1.** property details" in meta["system_prompt"]


def test_combined_marla_request_asks_for_location():
    memory = ConversationMemory()
    request = "mujhy ghr chahiye 10 marla ka"
    memory.add("user", request)
    answer, _ = make_voice_answer(request, memory)
    assert "kis location" in answer
    assert memory.preferences["search_flow"] == "location"


def test_property_list_formats_number_only_in_bold():
    answer = _format_property_list("Options: **1. PROP-1: House, Lahore, agent Ali** 2. **PROP-2: House, Islamabad, agent Sara**")
    assert answer == "Options:\n**1.** PROP-1: House, Lahore\n**2.** PROP-2: House, Islamabad"


def test_standalone_marla_answer_moves_to_location():
    memory = ConversationMemory()
    memory.preferences["search_flow"] = "marla"
    answer, _ = make_voice_answer("10", memory)
    assert memory.preferences["area_marla"] == 10.0
    assert memory.preferences["search_flow"] == "location"
    assert "kis location" in answer


def test_visit_asks_date_then_time_then_confirms_agent():
    memory = ConversationMemory()
    memory.preferences.update({
        "listing_ids": ["PROP-1018"],
        "listing_agents": ["Bilal Chaudhry"],
        "selected_property_id": "PROP-1018",
        "selected_agent": "Bilal Chaudhry",
    })
    date_prompt, _ = make_voice_answer("visit schedule karni hai", memory)
    assert "kis date" in date_prompt
    time_prompt, _ = make_voice_answer("29 august", memory)
    assert "kis time" in time_prompt
    confirmation, _ = make_voice_answer("2 bjy", memory)
    assert "Bilal Chaudhry" in confirmation
    assert "share kar di" in confirmation


def test_flat_preference_is_remembered():
    memory = ConversationMemory()
    memory.add("user", "mujhy flat chahiye")
    assert memory.preferences["property_type"] == "Flat"


def test_flat_request_does_not_ask_for_marla():
    memory = ConversationMemory()
    memory.add("user", "mujhy flat chahiye")
    answer, _ = make_voice_answer("mujhy flat chahiye", memory)
    assert answer == "Flat rent par chahiye ya khareedna hai?"
    assert "marla" not in answer
    fresh_memory = ConversationMemory()
    fresh_memory.preferences["greeting_sent"] = True
    fresh_memory.preferences["property_type"] = "Flat"
    answer, _ = make_voice_answer("flat dekhna ha", fresh_memory)
    assert answer == "Flat rent par chahiye ya khareedna hai?"
    assert "marla" not in answer


def test_flat_search_asks_location_without_marla():
    memory = ConversationMemory()
    memory.add("user", "mujhy flat chahiye")
    make_voice_answer("mujhy flat chahiye", memory)
    memory.add("user", "rent ke liye")
    answer, _ = make_voice_answer("rent ke liye", memory)
    assert "flat dekhna chahenge" in answer
    assert "marla" not in answer


def test_switching_from_flat_to_house_updates_property_type():
    memory = ConversationMemory()
    memory.add("user", "mujhy flat chahiye")
    memory.add("user", "acha ghr ka bhi bta do")
    assert memory.preferences["property_type"] == "House"
    answer, _ = make_voice_answer("acha ghr ka bhi bta do", memory)
    assert answer == "Aap kitne marla ka ghar chahte hain?"


if __name__ == "__main__":
    test_memory_budget_and_locality(); test_memory_persists_across_turns(); test_objections(); test_query_contains_memory(); print("All Day-3 core tests passed.")
