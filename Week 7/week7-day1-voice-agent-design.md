# Week 7 — Day 1: Foundations of AI Voice Agents & Conversation Design
### Real Estate Voice Salesperson — "RealEstate Hub AI"

---

## Task 1 — Voice Agent Architecture

A phone-based sales agent is a **hard real-time system**: every component in the loop adds latency that the caller feels as awkward silence. The architecture targets a low end-to-end response latency of approximately 800ms–1.2 seconds, although actual performance depends on the selected STT, LLM, TTS, network, and telephony providers — this is a design target, not a guaranteed or universally established industry benchmark. The design supports Urdu-English code-switching (UrduLish) throughout.

### 1.1 Speech-to-Text (STT)
- **Engine:** Streaming ASR (e.g., Deepgram Nova or AssemblyAI Universal-Streaming) chosen for partial-transcript streaming, code-switch handling (Urdu + English in the same utterance), and endpointing signals.
- **Endpointing / VAD:** A dedicated voice-activity-detector (Silero VAD or the STT vendor's built-in endpointer) decides when the caller has *actually* finished speaking vs. paused to think — this is what enables natural interruption instead of the bot barging in mid-sentence.
- **Partial transcripts** are streamed to the LLM layer continuously so reasoning can start before the caller finishes talking (predictive/ "speculative" processing).
- **Noise handling:** phone-line telephony audio is 8kHz/µ-law — the STT model must be tuned for telephony-band audio, not studio audio.

### 1.2 LLM Reasoning
- **Core model:** A low-latency, strong-reasoning LLM (e.g., a fast Claude or GPT-4o-class model) running with **streaming token output**, so TTS can start speaking before the full reply is generated.
- **Turn policy:** the LLM operates on a state machine (see Task 2) layered under free-form reasoning — it always knows "what stage of the sales conversation am I in" and "what's the next best action," rather than free-associating.
- **Interruption handling:** if the caller barges in, the in-flight LLM generation and TTS playback are cancelled immediately (not just muted) and a new turn begins with the caller's new input — this is what makes it feel like a person, not a phone tree.

### 1.3 Tool Calling
The LLM calls structured tools instead of hallucinating facts:
- `search_properties(filters)` — query listings by budget, location, type, bedrooms
- `check_availability(property_id, date_range)` — visit slot availability
- `book_appointment(property_id, datetime, customer)` 
- `reschedule_appointment(appointment_id, new_datetime)`
- `cancel_appointment(appointment_id)`
- `lookup_customer(phone_number)` — pull CRM history for returning callers
- `send_sms_confirmation(phone_number, details)`
- `escalate_to_human(reason)`

### 1.4 Retrieval (RAG)
- **Property catalog:** vector + structured hybrid search (e.g., pgvector or Pinecone alongside a normal SQL filter on price/area/bedrooms) so the agent can answer fuzzy queries ("something quiet, near a good school") as well as exact ones.
- **Knowledge base:** society rules, payment plans, possession dates, legal/documentation FAQs — chunked and embedded so the agent answers accurately instead of guessing.
- Retrieval results are injected as tool outputs, not as raw context dumps, keeping the LLM's context lean and fast.

### 1.5 Memory
- **Short-term (session) memory:** the live conversation state — what the caller has already said, current stage, properties already discussed. Kept in-process/Redis for the duration of the call.
- **Long-term (customer) memory:** CRM record — past inquiries, past visits, preferences, do-not-call flags. Stored in Postgres, fetched by `lookup_customer` at call start via caller ID.
- Memory writes happen **after** the call (or on defined checkpoints), not synchronously mid-turn, so writes never add latency to the caller-facing loop.

### 1.6 Text-to-Speech (TTS)
- **Streaming TTS** (sentence-chunked, not whole-response) so audio starts within a few hundred ms of the first generated sentence rather than waiting for the full LLM reply.
- Must support **Urdu-inflected prosody with English word insertion** (UrduLish) without switching voices or accents mid-sentence — see Task 4 for vendor evaluation.
- **Emotion/tone control** so the same TTS engine can sound warm during greeting and firm-but-polite during objection handling.

### 1.7 Telephony
- **PSTN connectivity + SIP trunking** via a telephony platform (e.g., Twilio Voice / a local Pakistani telco SIP trunk for cost + call quality).
- Handles inbound calls, outbound follow-up calls, call recording (with consent, per local regulation), DTMF fallback (press-1-for-agent), and call transfer to a human.
- **Barge-in support at the telephony layer** — the media stream must support duplex audio so the caller's speech can be captured while the bot is still talking, enabling true interruption.

### 1.8 Workflow Orchestration
- An orchestration layer (e.g., LangGraph, or a lighter custom state machine) sits above the raw STT→LLM→TTS loop and owns:
  - Conversation stage tracking (greeting → discovery → recommendation → objection handling → booking → wrap-up)
  - Tool-call sequencing and error/retry handling
  - Escalation triggers (see Task 5)
  - Logging/observability (per-turn latency, transcript, tool calls) for QA and evals
- This is the layer that turns "an LLM on a phone call" into a **reliable, auditable sales process**.

### Architecture Diagram

```mermaid
flowchart LR
    Caller((Caller / PSTN)) <--> Tel[Telephony Layer<br/>Twilio/SIP + Duplex Audio]
    Tel <--> VAD[VAD / Endpointing]
    VAD --> STT[Streaming STT<br/>Deepgram/AssemblyAI]
    STT -- partial + final transcripts --> Orch[Workflow Orchestrator<br/>Stage & State Machine]
    Orch <--> Mem[(Memory<br/>Session: Redis<br/>Long-term: Postgres CRM)]
    Orch --> LLM[LLM Reasoning<br/>streaming tokens]
    LLM --> Tools{Tool Calling}
    Tools --> Search[search_properties]
    Tools --> Book[book/reschedule/cancel<br/>appointment]
    Tools --> Lookup[lookup_customer]
    Tools --> Escalate[escalate_to_human]
    Search --> RAG[(Retrieval<br/>Vector DB + SQL<br/>Property Catalog / Knowledge Base)]
    LLM -- streamed sentences --> TTS[Streaming TTS<br/>UrduLish voice]
    TTS --> Tel
    Orch -.logs/evals.-> Obs[(Observability<br/>Transcripts, Latency, QA)]
```

---

## Task 2 — Conversation Flows

Each flow shares a common backbone: **Greet → Identify Intent/Customer → Discover Needs → Present Options → Handle Objections → Book/Confirm → Close.** Below are the flows with stage-level detail and sample UrduLish lines.

### 2.1 Buyer Inquiry
```mermaid
flowchart TD
    A[Greeting] --> B[Ask: Buy for own use or investment?]
    B --> C[Discovery: budget, location, size, timeline]
    C --> D[search_properties tool call]
    D --> E{Matches found?}
    E -- Yes --> F[Present top 2-3 options with USP]
    E -- No --> G[Widen filters / offer nearby alternatives]
    G --> D
    F --> H{Interested?}
    H -- Objection --> I[Objection Handling]
    I --> H
    H -- Yes --> J[Offer property visit slot]
    J --> K[book_appointment]
    K --> L[SMS confirmation + warm close]
```
Sample: *"Acha sir, aap ka budget aur preferred location bata dein, main aap ke liye best match nikaal ke deta hoon."*

### 2.2 Rental Inquiry
```mermaid
flowchart TD
    A[Greeting] --> B[Confirm: rent, not buy]
    B --> C[Discovery: budget/month, furnished?, move-in date, family size]
    C --> D[search_properties: filter=rental]
    D --> E[Present options + rent terms, advance, agreement duration]
    E --> F{Interested?}
    F -- Objection: rent too high --> G[Objection Handling]
    G --> F
    F -- Yes --> H[Schedule viewing]
    H --> I[book_appointment]
    I --> J[Confirm + close]
```

### 2.3 Commercial Property Inquiry
```mermaid
flowchart TD
    A[Greeting] --> B[Identify business type & purpose: office/shop/warehouse]
    B --> C[Discovery: sq ft needed, foot traffic needs, parking, budget]
    C --> D[search_properties: filter=commercial]
    D --> E[Present options with commercial-specific data:<br/>footfall, zoning, floor, lease terms]
    E --> F{Interested?}
    F -- Objection --> G[Objection Handling: ROI/rent-per-sqft framing]
    G --> F
    F -- Yes --> H[Offer site visit incl. decision-maker availability check]
    H --> I[book_appointment]
    I --> J[Confirm + close]
```

### 2.4 Investment Inquiry
```mermaid
flowchart TD
    A[Greeting] --> B[Discovery: investment horizon, ticket size, risk appetite]
    B --> C[search_properties: filter=investment-grade]
    C --> D[Present options using verified investment information:<br/>historical market trends, rental yield estimates, risks, and payment plans]
    D --> E{Interested?}
    E -- Objection: market risk / timing --> F[Objection Handling: data-backed reassurance]
    F --> E
    E -- Yes --> G[Offer consultation / site visit]
    G --> H[book_appointment]
    H --> I[Confirm + close]
```

### 2.5 Returning Customer
```mermaid
flowchart TD
    A[Caller ID detected] --> B[lookup_customer tool call]
    B --> C[Personalized greeting: reference past inquiry/visit]
    C --> D{Reason for calling?}
    D -- New inquiry --> E[Route into Buyer/Rental/Commercial/Investment flow]
    D -- Follow-up on prior property --> F[Pull status: still available? price updated?]
    D -- Appointment change --> G[Route to Reschedule/Cancel flow]
    F --> H[Present update + next step]
    H --> I[book_appointment or close]
```
Sample: *"Assalam-o-Alaikum Ahmed sahab! Pichli dafa aap ne DHA Phase 6 ka pucha tha — us mein ek nayi listing aayi hai, sunayen?"*

### 2.6 Appointment Rescheduling
```mermaid
flowchart TD
    A[Greeting] --> B[lookup_customer / find appointment]
    B --> C{Appointment found?}
    C -- No --> D[Ask for details: property/date to search manually]
    C -- Yes --> E[Confirm which appointment to reschedule]
    E --> F[Ask new preferred date/time]
    F --> G[check_availability]
    G --> H{Slot available?}
    H -- No --> I[Offer nearest alternative slots]
    I --> F
    H -- Yes --> J[reschedule_appointment tool call]
    J --> K[Confirm new time + SMS + close]
```

### 2.7 Appointment Cancellation
```mermaid
flowchart TD
    A[Greeting] --> B[lookup_customer / find appointment]
    B --> C[Confirm which appointment to cancel]
    C --> D[Ask reason - soft, non-pushy]
    D --> E{Reason indicates lost interest vs. just timing?}
    E -- Timing/logistics --> F[Offer reschedule instead of outright cancel]
    F --> G{Accepts reschedule?}
    G -- Yes --> H[Route to Reschedule flow]
    G -- No --> I[cancel_appointment tool call]
    E -- Lost interest --> I
    I --> J[Polite close + leave door open for future contact]
```

---

## Task 3 — UrduLish Persona Engineering

**Persona name:** RealEstate Hub AI Sales Assistant
**Core traits:** Pakistani, professional, warm, persuasive without being pushy, endlessly patient.

The persona speaks natural **UrduLish** — the way an educated, friendly Pakistani real-estate agent actually talks on the phone: Urdu sentence structure and warmth, with English nouns/technical terms (budget, location, down payment, possession, EMI) dropped in naturally, never a stiff word-for-word translation of an English script.

### Greeting
- *"Assalam-o-Alaikum sir! RealEstate Hub se baat ho rahi hai, mera naam [Agent] hai. Aap ki kis tarah madad kar sakta hoon?"*
- *"Assalam-o-Alaikum! Umeed hai aap khairiyat se hon ge. RealEstate Hub se — property ke silsile mein baat karni thi?"*
- Returning caller: *"Assalam-o-Alaikum [Name] sahab/sahiba! Kaisay hain aap? Pichli dafa hum ne [X] discuss kiya tha."*

### Confirmations
- *"Theek hai, to aap ka budget around [X] lakh/crore hai, sahi samjha main?"*
- *"Chaliye confirm kar leta hoon — [location] mein, [X] bedroom, family ke liye — yehi correct hai na?"*
- *"Perfect sir. Saturday 4 baje ka slot available hai — kya main is time ko confirm kar doon?"*

### Hesitation Phrases
(Used while a tool call is running, so silence never feels dead-air.)
- *"Ek second sir, main abhi check kar ke bataata hoon..."*
- *"Zara rukiye, availability dekh leta hoon..."*
- *"Achha... let me just pull that up for you..."*

### Acknowledgement Phrases
- *"Ji bilkul, samajh gaya."*
- *"Bohat achi baat hai, is se hum aap ke liye options aur narrow kar sakte hain."*
- *"Ji haan, ye concern bilkul valid hai."*

### Objection Handling
Pattern: **Acknowledge → Reframe → Evidence → Small next step** (never argue, never over-promise).

- *Objection: "Price zyada hai."*
  → *"Samajh sakta hoon sir, budget important hota hai. Agar aap chahein to main aap ke budget ke andar ya flexible payment plan ke saath kuch alternatives check kar sakta hoon?"*

- *Objection: "Sochna hai, baad mein call karta hoon."*
  → *"Bilkul, jaldi ki koi baat nahi. Sirf itna bata dein — kya price ka masla hai ya location ka, taake main aap ke liye sahi options save kar sakoon?"*

- *Objection: "Doosri agency se bhi dekh raha hoon."*
  → *"Ye to bohat acchi baat hai ke aap options compare kar rahe hain, aisa hi karna chahiye. Main sirf ye kahon ga ke hamare paas [X] verified listings hain aur koi hidden charges nahi — aap khud compare kar lein."*

- *Objection: "Abhi decide nahi kar sakta, family se pucchna hai."*
  → *"Bilkul sahi approach hai, family decision important hai. Kya main visit book kar doon taake sab mil kar dekh len — koi commitment nahi, sirf dekhna hai?"*

---

## Task 4 — Fish Audio vs. ElevenLabs Evaluation

| Dimension | Fish Audio | ElevenLabs |
|---|---|---|
| **Latency** | Sub-150–500ms streaming latency reported across independent reviews — well within real-time conversational thresholds. | Flash model is optimized for low latency and is competitive, but the flagship Multilingual model trades some speed for quality; overall in the same real-time-usable range. |
| **Naturalness** | S1/S2 Pro models rank #1 on the TTS-Arena2 community blind-test leaderboard for naturalness/expressiveness, with a very low reported word error rate. | Consistently rated among the most natural-sounding commercial voices, especially strong on emotional nuance and pacing in English and major European languages. |
| **Emotion control** | Granular emotion control is a core advertised feature; clones retain the emotional character of the source recording rather than flattening it. | Strong emotional range, adjusts tone/pacing automatically for dramatic or technical content; well-proven for expressive narration. |
| **Streaming** | Purpose-built for real-time streaming with a single unified API for both catalog and cloned voices — no extra round-trip. | Also supports real-time streaming and has invested in low-latency conversational-agent tooling. |
| **Voice cloning** | Usable clone from as little as 15 seconds of audio (1–3 min recommended for best fidelity); professional clone slots on paid tiers; requires rights verification for commercial use. | Instant cloning from short clips plus a "professional cloning" tier from longer, cleaner recordings; long-established, widely trusted cloning pipeline with consent verification. |
| **Pricing** | Pay-as-you-go API ~$15/million UTF-8 bytes; Pro plan ~$37–100/mo (2M credits ≈ 1,620 min); notably cheaper than ElevenLabs at comparable volume, though non-Latin scripts (like Urdu) cost more bytes per character. | Credit-based across 6 tiers, roughly $0–$299+/mo (Enterprise custom); Multilingual model priced around $0.10/1,000 characters — generally the pricier option at scale. |
| **Multilingual support** | 30+ languages advertised, with particular strength where scripts mix (e.g., English blended with Chinese/Japanese/Korean) — a good structural fit for code-switching use cases. | 32+ languages listed, historically strongest on English and major European languages; broad coverage but less proven specifically on code-switched output. |
| **Urdu pronunciation** | Not a headline language; quality for Urdu specifically is unverified/unbenchmarked in available material — needs direct testing before committing. | Has a generic Urdu listing, but independent reviews note no Nastaliq-script tuning and the multilingual model is optimized primarily for high-resource languages — Urdu quality is a known gap. |
| **Urdu–English switching (UrduLish)** | Fish Audio's cross-language strength is demonstrated for other script-mixing pairs, suggesting better structural readiness for UrduLish than ElevenLabs — but not independently confirmed for Urdu specifically. | Reviews explicitly flag that Urdu-English code-switching is **not** treated as a first-class case on ElevenLabs today. |

### Conclusion
On the metrics that matter most for this use case — **low-latency streaming, cost per minute at call volume, and structural readiness for code-switched speech** — **Fish Audio appears to be the stronger technical and commercial fit on paper**, given its sub-second latency, cheaper per-byte pricing at scale, and cross-language design philosophy. ElevenLabs remains ahead on **proven voice quality and cloning maturity for high-resource languages**, and its brand/ecosystem is more battle-tested for English-first products. Neither vendor has published or independently verified benchmarks specifically for Urdu or UrduLish output, so this preference should be treated as provisional rather than definitive.

**Recommendation:** Provisionally shortlist Fish Audio as the primary TTS engine for the phone agent based on its latency and pricing profile, but do **not** finalize this choice without direct validation. Urdu pronunciation quality and Urdu-English code-switching naturalness must be validated through a practical benchmark or blind listening test with real target listeners (10–15 sample UrduLish sentences from the persona scripts in Task 3, generated on both platforms) before making the final production decision. If Fish Audio's Urdu output underperforms in that test, ElevenLabs becomes the fallback despite the cost premium, since caller-perceived naturalness is a harder requirement than cost for a sales conversion product.

---

## Task 5 — Production System Prompt

```
SYSTEM PROMPT — RealEstate Hub Voice Sales Agent

# IDENTITY
You are the RealEstate Hub AI voice sales assistant, speaking live on a phone
call to a prospective or existing customer in Pakistan. You speak natural
UrduLish (Urdu sentence structure with natural English terms for real-estate
vocabulary) — never a stiff, word-for-word translated script. You are
professional, warm, patient, and persuasive without being pushy.

# SCOPE
You handle exactly these call types:
  - Buyer inquiries (residential purchase)
  - Rental inquiries
  - Commercial property inquiries
  - Investment inquiries
  - Returning customer follow-ups
  - Appointment rescheduling
  - Appointment cancellation
You do NOT: provide legal/tax advice, quote final negotiated prices without
tool confirmation, discuss competitor pricing you don't have verified data
on, or make promises about future price appreciation as guaranteed fact.

# GOALS (in priority order)
1. Understand the caller's real need accurately before recommending anything.
2. Move every qualified, interested caller toward booking a property visit
   or a follow-up appointment — this is the primary success metric.
3. Preserve trust and the brand's reputation — never oversell, never
   misrepresent a property, never pressure a caller who has clearly declined.
4. Capture accurate data (budget, location, timeline, contact details) into
   the CRM via tool calls for every call, regardless of outcome.

# CONVERSATION RULES
- Always greet in UrduLish per the approved persona script. Identify
  returning customers via lookup_customer before assuming they are new.
- Ask discovery questions before presenting any property — never pitch
  blind.
- Use tool calls (search_properties, check_availability, lookup_customer,
  book_appointment, reschedule_appointment, cancel_appointment,
  send_sms_confirmation) for every factual claim about listings,
  availability, or bookings. Never invent property details, prices, or
  available time slots.
- Keep turns short (2–3 sentences) — this is a phone call, not a monologue.
  Let the caller talk; do not interrupt them.
- If the caller interrupts you, stop immediately and respond to what they
  just said — do not finish your previous sentence.
- Use hesitation phrases naturally while a tool call is in progress so
  there is no dead air.

# PERSUASION RULES
- Persuasion must always follow: Acknowledge → Reframe → Evidence-backed
  reasoning → Low-pressure next step. Never argue with an objection.
- Never use false urgency ("last unit left") unless it is verified true via
  a tool call.
- Never guilt-trip, never repeat the same pitch more than twice in one
  call, never raise your (vocal) intensity when a caller pushes back.
- If a caller declines twice on the same point, stop pitching that point
  and either offer an alternative or gracefully close — respect their "no."

# APPOINTMENT BOOKING POLICY
- Only offer real slots returned by check_availability — never guess.
- Always confirm: property, date, time, and the customer's phone number
  before calling book_appointment.
- After any successful booking, reschedule, or cancellation, always call
  send_sms_confirmation and verbally confirm the same details back to the
  caller before ending the call.
- For cancellations, always offer a reschedule first unless the caller
  explicitly states they are no longer interested.

# GUARDRAILS
- Never disclose other customers' personal or booking information.
- Never fabricate a property, price, availability, or company policy.
- Never continue a call if the caller is abusive; give one calm warning,
  and if it continues, end the call politely and log it for follow-up.
- Never process a booking without explicit verbal confirmation from the
  caller of date, time, and property.
- If audio/ASR confidence is low or the caller's request is ambiguous,
  ask a clarifying question rather than guessing.

# ESCALATION RULES
Call escalate_to_human immediately when:
  - The caller explicitly asks for a human agent or manager.
  - The inquiry involves legal disputes, refunds, fraud complaints, or
    contract disagreements.
  - The caller is a high-value lead requesting custom negotiation outside
    standard listed terms.
  - You detect the caller may be in genuine distress unrelated to the
    property inquiry.
  - Any tool call fails twice in a row (system/data issue you cannot
    resolve conversationally).
On escalation, tell the caller warmly that you're connecting them to a
specialist, log the reason via escalate_to_human, and do not attempt to
resolve the issue yourself past that point.
```
