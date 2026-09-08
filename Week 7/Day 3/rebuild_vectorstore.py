"""Cleanly rebuild the persistent Chroma collection.

Run this whenever the knowledge-base CSVs or embedding model changes. Mixing
vectors from different embedding models in one collection is unsafe, so the
existing collection is always removed before re-indexing.
"""
from rag_pipeline import load_documents, build_chunks, get_vectorstore, index_chunks
from config import CHROMA_DIR, COLLECTION_NAME, GEMINI_EMBEDDING_MODEL


def main():
    print(f"Rebuilding vector store: {CHROMA_DIR}")
    print(f"Collection: {COLLECTION_NAME}")
    print(f"Embedding model: {GEMINI_EMBEDDING_MODEL}")

    vectorstore = get_vectorstore()
    try:
        vectorstore.delete_collection()
        print("Deleted previous collection.")
    except Exception as exc:
        print(f"No previous collection was removed ({exc}); creating a fresh one.")

    vectorstore = get_vectorstore()
    docs = load_documents()
    chunks = build_chunks(docs, chunk_size=800, overlap=120)
    print(f"Loaded {len(docs)} source documents and built {len(chunks)} chunks.")

    count = index_chunks(vectorstore, chunks)
    print(f"Indexed {count} chunks successfully.")
    print(f"Persistent Chroma directory: {CHROMA_DIR}")


if __name__ == "__main__":
    main()
