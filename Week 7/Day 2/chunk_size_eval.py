"""
Task 2 (cont.): Chunk-size evaluation, re-run against real gemini-embedding-001
vectors (previously run against local TF-IDF+SVD vectors -- the evaluation
logic and question set are UNCHANGED, only the embedding call is real now).

Requires GOOGLE_API_KEY in .env -- makes real embedding API calls (one
per chunk, per chunk size), so this cannot execute inside the build sandbox
(no network route to Google's API here). Run it locally with a real key;
per section 11 of the upgrade spec, do not assume 800 wins again just
because it won under the old local embeddings -- re-run this and read the
printed table before picking a chunk size for production.
Rate limits: indexing is done in rate-limited batches (see
rag_pipeline.index_chunks) to stay under the free-tier embedding quota
(100 requests/minute), and this script also pauses briefly between the
three chunk-size runs as a safety buffer. A full run can take several
minutes on the free tier -- that's expected, not a hang.
"""
import time
from langchain_chroma import Chroma
from rag_pipeline import load_documents, build_chunks, index_chunks
from config import get_embeddings

docs = load_documents()

eval_pairs = [
    ("What documents are required to buy a property?", "faq_0"),
    ("How much token money is paid to book a property?", "faq_1"),
    ("Can overseas Pakistanis buy property here?", "faq_2"),
    ("Is rent price negotiable?", "faq_6"),
    ("How is plot size measured in marla?", "faq_7"),
    ("What happens if I miss my property visit?", "faq_9"),
    ("Do you guarantee investment returns?", "faq_10"),
    ("What are the transfer and registration charges?", "faq_12"),
    ("How do I cancel my scheduled appointment?", "faq_17"),
    ("What amenities does Bahria Town Rawalpindi have?", "locality_Bahria Town_Rawalpindi"),
    ("What schools are near F-10 Islamabad?", "locality_F-10_Islamabad"),
    ("What hospitals are close to DHA Defence Lahore?", "locality_DHA Defence_Lahore"),
    ("Tell me about the standard 3 year installment plan", "plan_Standard 3-Year Installment Plan"),
    ("Is full cash payment required for resale properties?", "plan_Full Cash / Ready Property"),
]

if __name__ == "__main__":
    embeddings = get_embeddings()
    print(f"{'Chunk Size':>10} | {'#Chunks':>8} | {'Hit@3':>7} | {'Hit@1':>7}")
    chunk_sizes = [150, 400, 800]
    for idx, cs in enumerate(chunk_sizes):
        chunks = build_chunks(docs, chunk_size=cs, overlap=int(cs * 0.15))
        store = Chroma(persist_directory=f"./chroma_eval_{cs}", collection_name=f"eval_{cs}",
                        embedding_function=embeddings)
        print(f"\nIndexing chunk_size={cs} ({len(chunks)} chunks)...")
        index_chunks(store, chunks)

        hits3 = hits1 = 0
        for q, expected_doc in eval_pairs:
            results = store.similarity_search(q, k=3)
            top = [d.metadata.get("doc_id") for d in results]
            hits3 += expected_doc in top
            hits1 += expected_doc in top[:1]
        n = len(eval_pairs)
        print(f"{cs:>10} | {len(chunks):>8} | {hits3/n:>6.0%} | {hits1/n:>6.0%}")

        if idx < len(chunk_sizes) - 1:
            print("Pausing 20s before next chunk size (rate-limit safety buffer)...")
            time.sleep(20)