"""Semantic RAG layer for RealEstate Hub.

Pipeline: CSV knowledge sources -> documents -> 800/120 chunks -> Gemini
embeddings -> persistent ChromaDB -> top-k semantic retrieval.

Property-level transactional facts (price, area, bedrooms, availability, agent)
remain in SQL; descriptive knowledge (localities, amenities, schools,
hospitals, developers, payment plans, FAQs) is embedded for semantic search.
"""
from __future__ import annotations

import time
from typing import Iterable, TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from langchain_chroma import Chroma

from config import (
    CHROMA_DIR,
    COLLECTION_NAME,
    CSV_DIR,
    EMBEDDING_BATCH_DELAY_SECONDS,
    EMBEDDING_BATCH_SIZE,
    PROJECT_ROOT,
    RETRIEVAL_K,
    RETRIEVAL_MIN_SCORE,
    get_embeddings,
)


KB_FILES = {
    "locations": CSV_DIR / "locations.csv",
    "amenities": CSV_DIR / "amenities.csv",
    "schools": CSV_DIR / "schools.csv",
    "hospitals": CSV_DIR / "hospitals.csv",
    "developers": CSV_DIR / "developers.csv",
    "payment_plans": CSV_DIR / "payment_plans.csv",
    "faqs": CSV_DIR / "faqs.csv",
}


def _read_csv(name: str) -> pd.DataFrame:
    path = KB_FILES[name]
    if not path.exists():
        raise FileNotFoundError(f"Knowledge-base file not found: {path}")
    return pd.read_csv(path)


def load_documents() -> list[dict]:
    """Build semantic documents with source metadata preserved."""
    locations = _read_csv("locations")
    amenities = _read_csv("amenities")
    schools = _read_csv("schools")
    hospitals = _read_csv("hospitals")
    developers = _read_csv("developers")
    payment_plans = _read_csv("payment_plans")
    faqs = _read_csv("faqs")

    docs: list[dict] = []
    for _, loc in locations.iterrows():
        lf = str(loc["locality_full"])
        am = amenities.loc[amenities["locality_full"] == lf, "amenity"].dropna().tolist()
        sc = schools[schools["locality_full"] == lf]
        ho = hospitals[hospitals["locality_full"] == lf]
        dv = developers[
            (developers["locality_name"] == loc["locality_name"])
            & (developers["city"] == loc["city"])
        ]
        developer_name = str(dv.iloc[0]["developer_authority"]) if len(dv) else None

        text = f"{loc['locality_name']}, {loc['city']}: {loc['description']} "
        if len(dv):
            text += f"Developed/regulated by: {developer_name}. {dv.iloc[0]['profile']} "
        if am:
            text += f"Amenities include: {', '.join(am)}. "
        if len(sc):
            text += "Nearby schools: " + "; ".join(
                f"{r.school_name} ({r.level}, ~{r.distance_km_est}km)"
                for r in sc.itertuples()
            ) + ". "
        if len(ho):
            text += "Nearby hospitals: " + "; ".join(
                f"{r.hospital_name} ({r.specialty}, ~{r.distance_km_est}km)"
                for r in ho.itertuples()
            ) + ". "

        docs.append({
            "doc_id": f"locality_{loc['locality_name']}_{loc['city']}",
            "source_type": "locality_profile",
            "text": text.strip(),
            "city": loc["city"],
            "locality": loc["locality_name"],
            "developer": developer_name,
            "source": "locations.csv + related locality KB tables",
        })

    for _, p in payment_plans.iterrows():
        text = (
            f"Payment Plan: {p['plan_name']} (applicable to {p['applicable_to']}). "
            f"Down payment {p['down_payment_pct']}%, confirmation {p['confirmation_pct']}%, "
            f"{p['quarterly_installments']} quarterly installments at "
            f"{p['installment_pct_each']}% each, possession charges "
            f"{p['possession_charges_pct']}%. Note: {p['notes']}"
        )
        docs.append({
            "doc_id": f"plan_{p['plan_name']}",
            "source_type": "payment_plan",
            "text": text,
            "city": None,
            "locality": None,
            "developer": None,
            "source": "payment_plans.csv",
        })

    for i, faq in faqs.iterrows():
        docs.append({
            "doc_id": f"faq_{i}",
            "source_type": "faq",
            "text": f"Q: {faq['question']} A: {faq['answer']}",
            "city": None,
            "locality": None,
            "developer": None,
            "source": "faqs.csv",
        })

    return docs


def chunk_text(text: str, chunk_size: int, overlap: int = 0) -> list[str]:
    """Character chunker used by the evaluated Day-2 configuration."""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be > 0")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap must be >= 0 and smaller than chunk_size")
    if len(text) <= chunk_size:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start = end - overlap
    return chunks


def build_chunks(docs: Iterable[dict], chunk_size: int = 800, overlap: int = 120) -> list[dict]:
    chunks: list[dict] = []
    for doc in docs:
        for i, piece in enumerate(chunk_text(doc["text"], chunk_size, overlap)):
            chunks.append({
                "chunk_id": f"{doc['doc_id']}__c{i}",
                "doc_id": doc["doc_id"],
                "source_type": doc["source_type"],
                "text": piece,
                "city": doc.get("city"),
                "locality": doc.get("locality"),
                "developer": doc.get("developer"),
                "source": doc.get("source"),
            })
    return chunks


def get_vectorstore():
    """Return persistent ChromaDB using the configured Gemini embeddings."""
    from langchain_chroma import Chroma
    return Chroma(
        persist_directory=CHROMA_DIR,
        collection_name=COLLECTION_NAME,
        embedding_function=get_embeddings(),
    )


def index_chunks(vectorstore, chunks: list[dict]) -> int:
    """Store chunks with stable IDs and complete retrieval metadata."""
    if not chunks:
        return 0
    from langchain_core.documents import Document
    documents = [
        Document(
            page_content=c["text"],
            metadata={
                "chunk_id": c["chunk_id"],
                "doc_id": c["doc_id"],
                "source_type": c["source_type"],
                "city": c.get("city") or "",
                "locality": c.get("locality") or "",
                "developer": c.get("developer") or "",
                "source": c.get("source") or "",
            },
        )
        for c in chunks
    ]
    ids = [c["chunk_id"] for c in chunks]
    # Make indexing idempotent if this function is called twice without a full rebuild.
    try:
        vectorstore.delete(ids=ids)
    except Exception:
        pass
    if EMBEDDING_BATCH_SIZE <= 0:
        raise ValueError("EMBEDDING_BATCH_SIZE must be greater than zero")
    if EMBEDDING_BATCH_DELAY_SECONDS < 0:
        raise ValueError("EMBEDDING_BATCH_DELAY_SECONDS cannot be negative")

    for start in range(0, len(documents), EMBEDDING_BATCH_SIZE):
        end = start + EMBEDDING_BATCH_SIZE
        vectorstore.add_documents(documents=documents[start:end], ids=ids[start:end])
        if end < len(documents) and EMBEDDING_BATCH_DELAY_SECONDS:
            time.sleep(EMBEDDING_BATCH_DELAY_SECONDS)
    return len(documents)


def get_retriever(vectorstore, k: int | None = None):
    return vectorstore.as_retriever(search_kwargs={"k": k or RETRIEVAL_K})


def rag_retrieve(
    retriever,
    query: str,
    source_type_filter: list[str] | None = None,
    min_score: float | None = None,
) -> dict:
    """Retrieve scored chunks, optionally constrained to source types."""
    if not query or not query.strip():
        return {"query": query, "grounded": False, "chunks": [], "context_for_llm": ""}

    k = int(retriever.search_kwargs.get("k", RETRIEVAL_K))
    filt = {"source_type": {"$in": source_type_filter}} if source_type_filter else None
    scored = retriever.vectorstore.similarity_search_with_relevance_scores(
        query, k=k, filter=filt
    )

    threshold = RETRIEVAL_MIN_SCORE if min_score is None else min_score
    chunks = []
    for doc, score in scored:
        score = float(score)
        chunks.append({
            "chunk_id": doc.metadata.get("chunk_id"),
            "doc_id": doc.metadata.get("doc_id"),
            "source_type": doc.metadata.get("source_type"),
            "text": doc.page_content,
            "metadata": doc.metadata,
            "score": score,
        })

    # Apply the grounding threshold after scoring. This prevents the LLM from
    # receiving weakly related chunks as if they were verified evidence.
    grounded_chunks = [c for c in chunks if c["score"] >= threshold]
    return {
        "query": query,
        "grounded": bool(grounded_chunks),
        "chunks": grounded_chunks,
        "all_chunks": chunks,
        "context_for_llm": "\n".join(f"- {c['text']}" for c in grounded_chunks),
    }


if __name__ == "__main__":
    print("This module uses Gemini embeddings and persistent ChromaDB.")
    print("Run: python rebuild_vectorstore.py")
