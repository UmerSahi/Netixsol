"""
Task 2: RAG Pipeline -- Document Loader -> Chunking -> Embedding -> Vector
Store -> Retriever. (Answer generation lives in generate_answer.py.)

UPGRADE NOTE: this file previously used a local TF-IDF + Truncated SVD
(LSA) embedder and a hand-managed ChromaDB collection, because the build
sandbox had no network route to a hosted embedding endpoint. That local
approach is now REPLACED (not layered on top of) with real Gemini
embeddings via LangChain, per the production spec:

    embeddings = GoogleGenerativeAIEmbeddings(model="gemini-embedding-001")
    vectorstore = Chroma(persist_directory="./chroma_db",
                          collection_name="properties",
                          embedding_function=embeddings)
    retriever = vectorstore.as_retriever(search_kwargs={"k": 4})

The document loader and chunking logic (build_chunks, 800-char/120-overlap
default, evaluated in chunk_size_eval.py) are UNCHANGED -- only the
embedding/vector-store layer was swapped, per the "minimal change" upgrade
request. Everything downstream (structured_retrieval.py,
recommendation_engine.py, generate_answer.py, hallucination_eval.py) keeps
working against this same module's public functions.

Requires GOOGLE_API_KEY in .env (see config.py / .env.example). This
module makes real network calls to Google's embedding API when
`add_documents`/`query` run -- it cannot be exercised inside the build
sandbox (no network route to Google's API here), but the code is complete
and correct against the current `gemini-embedding-001` / langchain-google-
genai / langchain-chroma APIs, verified by import and signature inspection
in this session.
"""
import time
import pandas as pd
from langchain_core.documents import Document
from langchain_chroma import Chroma

from config import get_embeddings

CHROMA_DIR = "./chroma_db"
COLLECTION_NAME = "properties"


# ---------------------------------------------------------------
# 1. DOCUMENT LOADER (unchanged)
# ---------------------------------------------------------------
def load_documents():
    """Builds the unstructured corpus for vector retrieval: locality
    profiles, developer/authority profiles, payment plans, and FAQs.
    (Property-level facts -- price, availability, plot size -- are handled
    by SQL/Postgres, per Task 3's structured/semantic split, not embedded
    here.) Returns dicts with rich metadata (city, locality, developer,
    source_type) so the retriever can surface metadata alongside content,
    per spec section 3.
    """
    locations = pd.read_csv("locations.csv")
    amenities = pd.read_csv("amenities.csv")
    schools = pd.read_csv("schools.csv")
    hospitals = pd.read_csv("hospitals.csv")
    developers = pd.read_csv("developers.csv")
    payment_plans = pd.read_csv("payment_plans.csv")
    faqs = pd.read_csv("faqs.csv")

    docs = []
    for _, loc in locations.iterrows():
        lf = loc['locality_full']
        am = amenities[amenities['locality_full'] == lf]['amenity'].tolist()
        sc = schools[schools['locality_full'] == lf]
        ho = hospitals[hospitals['locality_full'] == lf]
        dv = developers[(developers['locality_name'] == loc['locality_name']) & (developers['city'] == loc['city'])]
        developer_name = dv.iloc[0]['developer_authority'] if len(dv) else None

        text = f"{loc['locality_name']}, {loc['city']}: {loc['description']} "
        if len(dv):
            text += f"Developed/regulated by: {developer_name}. {dv.iloc[0]['profile']} "
        if am:
            text += f"Amenities include: {', '.join(am)}. "
        if len(sc):
            text += "Nearby schools: " + "; ".join(
                f"{r.school_name} ({r.level}, ~{r.distance_km_est}km)" for r in sc.itertuples()) + ". "
        if len(ho):
            text += "Nearby hospitals: " + "; ".join(
                f"{r.hospital_name} ({r.specialty}, ~{r.distance_km_est}km)" for r in ho.itertuples()) + ". "

        docs.append({
            "doc_id": f"locality_{loc['locality_name']}_{loc['city']}",
            "source_type": "locality_profile", "text": text,
            "city": loc['city'], "locality": loc['locality_name'],
            "developer": developer_name, "source": "knowledge_base/locations.csv",
        })

    for _, p in payment_plans.iterrows():
        text = (f"Payment Plan: {p['plan_name']} (applicable to {p['applicable_to']}). "
                f"Down payment {p['down_payment_pct']}%, confirmation {p['confirmation_pct']}%, "
                f"{p['quarterly_installments']} quarterly installments at {p['installment_pct_each']}% each, "
                f"possession charges {p['possession_charges_pct']}%. Note: {p['notes']}")
        docs.append({
            "doc_id": f"plan_{p['plan_name']}", "source_type": "payment_plan", "text": text,
            "city": None, "locality": None, "developer": None,
            "source": "knowledge_base/payment_plans.csv",
        })

    for i, f in faqs.iterrows():
        docs.append({
            "doc_id": f"faq_{i}", "source_type": "faq", "text": f"Q: {f['question']} A: {f['answer']}",
            "city": None, "locality": None, "developer": None,
            "source": "knowledge_base/faqs.csv",
        })

    return docs


# ---------------------------------------------------------------
# 2. CHUNKING (unchanged -- 800 chars / 120 overlap was the evaluated best;
#    see chunk_size_eval.py. Do not change unless a new evaluation run
#    against gemini-embedding-001 shows a different size wins.)
# ---------------------------------------------------------------
def chunk_text(text, chunk_size, overlap=0):
    if len(text) <= chunk_size:
        return [text]
    chunks, start = [], 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start = end - overlap
        if overlap == 0 and end >= len(text):
            break
    return chunks


def build_chunks(docs, chunk_size=800, overlap=120):
    chunks = []
    for d in docs:
        for i, piece in enumerate(chunk_text(d['text'], chunk_size, overlap)):
            chunks.append({
                "chunk_id": f"{d['doc_id']}__c{i}", "doc_id": d['doc_id'],
                "source_type": d['source_type'], "text": piece,
                "city": d.get("city"), "locality": d.get("locality"),
                "developer": d.get("developer"), "source": d.get("source"),
            })
    return chunks


# ---------------------------------------------------------------
# 3. VECTOR STORE: real Gemini embeddings + persistent ChromaDB, via
#    LangChain -- the exact configuration specified in the upgrade.
# ---------------------------------------------------------------
def get_vectorstore():
    """Returns the langchain_chroma.Chroma vectorstore, configured with
    real Gemini embeddings (gemini-embedding-001 by default). Requires
    GOOGLE_API_KEY in .env (raises a clear RuntimeError otherwise, via
    config.get_embeddings())."""
    embeddings = get_embeddings()
    return Chroma(
        persist_directory=CHROMA_DIR,
        collection_name=COLLECTION_NAME,
        embedding_function=embeddings,
    )


def index_chunks(vectorstore, chunks, batch_size=40, pause_seconds=65, max_retries=5):
    """Embeds and stores the given chunks in rate-limited batches, so this
    stays under Google's free-tier embedding quota (100 requests/minute for
    gemini-embedding-001) instead of bursting every chunk at once and
    hitting a 429 RESOURCE_EXHAUSTED error partway through. Chunk metadata
    (doc_id, source_type, city, locality, developer, source, chunk_id) is
    preserved on each Document so the retriever can return it alongside
    content.

    If a batch still gets a 429 (e.g. quota was already partly used by
    another script), it backs off and retries a few times before giving up,
    using the server's own suggested retry delay when available.
    """
    documents = [
        Document(
            page_content=c["text"],
            metadata={
                "chunk_id": c["chunk_id"], "doc_id": c["doc_id"],
                "source_type": c["source_type"],
                "city": c.get("city") or "", "locality": c.get("locality") or "",
                "developer": c.get("developer") or "", "source": c.get("source") or "",
            },
        )
        for c in chunks
    ]
    ids = [c["chunk_id"] for c in chunks]
    total = len(documents)

    for start in range(0, total, batch_size):
        batch_docs = documents[start:start + batch_size]
        batch_ids = ids[start:start + batch_size]

        for attempt in range(max_retries):
            try:
                vectorstore.add_documents(documents=batch_docs, ids=batch_ids)
                break
            except Exception as e:
                is_rate_limit = "RESOURCE_EXHAUSTED" in str(e) or "429" in str(e)
                if not is_rate_limit or attempt == max_retries - 1:
                    raise
                wait = pause_seconds if attempt == 0 else pause_seconds * (attempt + 1)
                print(f"  Rate limit hit on batch {start}-{start + len(batch_docs)}, "
                      f"waiting {wait}s before retry {attempt + 1}/{max_retries}...")
                time.sleep(wait)

        done = min(start + batch_size, total)
        print(f"  Indexed {done}/{total} chunks.")
        if done < total:
            time.sleep(pause_seconds)  # let the per-minute quota window reset

    return total


def get_retriever(vectorstore, k=4):
    """Top-4 semantic retriever, per spec section 3."""
    return vectorstore.as_retriever(search_kwargs={"k": k})


def rag_retrieve(retriever, query, source_type_filter=None, min_score=None):
    """Runs semantic retrieval and returns content + metadata (+ relevance
    score) for each retrieved chunk, plus a flattened context string for the
    LLM prompt. Uses similarity_search_with_relevance_scores so callers
    (e.g. hallucination_eval.py) can apply a grounding threshold, same as
    the pre-upgrade local-embedding version did."""
    k = retriever.search_kwargs.get("k", 4)
    filt = {"source_type": {"$in": source_type_filter}} if source_type_filter else None
    scored = retriever.vectorstore.similarity_search_with_relevance_scores(query, k=k, filter=filt)

    chunks = [{"chunk_id": d.metadata.get("chunk_id"), "doc_id": d.metadata.get("doc_id"),
               "source_type": d.metadata.get("source_type"), "text": d.page_content,
               "metadata": d.metadata, "score": score} for d, score in scored]
    if min_score is not None:
        chunks = [c for c in chunks if c["score"] >= min_score]

    return {
        "query": query,
        "grounded": len(chunks) > 0,
        "chunks": chunks,
        "context_for_llm": "\n".join(f"- {c['text']}" for c in chunks),
    }


if __name__ == "__main__":
    print("This module requires GOOGLE_API_KEY (real network calls to "
          "Google's embedding API) -- run rebuild_vectorstore.py to index, "
          "then use rag_retrieve() to query.")