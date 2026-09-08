"""Central configuration for the RealEstate Hub Day-2 pipeline.

All paths are anchored to this project directory, so commands work whether
Python is launched from the project folder or from another working directory.
Secrets are read from .env and are never hard-coded.
"""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote_plus

from dotenv import load_dotenv
from sqlalchemy import create_engine

# backend/config.py lives one level inside the repo now, so the *repo* root
# (which holds .env, data/, storage/, results/) is one directory further up.
BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BACKEND_DIR.parent
load_dotenv(PROJECT_ROOT / ".env")

# Professional on-disk layout:
#   data/csv/            knowledge-base CSVs (source + generated)
#   data/db/              SQLite snapshot
#   data/vectorstores/     persistent + eval Chroma collections
#   storage/recordings/     uploaded call recordings
#   storage/evaluations/     saved human-evaluation JSON records
#   results/                 generated analysis/eval CSVs
DATA_DIR = PROJECT_ROOT / "data"
CSV_DIR = DATA_DIR / "csv"
DB_DIR = DATA_DIR / "db"
VECTORSTORE_DIR = DATA_DIR / "vectorstores"
STORAGE_DIR = PROJECT_ROOT / "storage"
RECORDINGS_DIR = STORAGE_DIR / "recordings"
EVALUATIONS_DIR = STORAGE_DIR / "evaluations"
RESULTS_DIR = PROJECT_ROOT / "results"
for _dir in (CSV_DIR, DB_DIR, VECTORSTORE_DIR, RECORDINGS_DIR, EVALUATIONS_DIR, RESULTS_DIR):
    _dir.mkdir(parents=True, exist_ok=True)

DB_BACKEND = os.getenv("DB_BACKEND", "sqlite").strip().lower()
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
GEMINI_EMBEDDING_MODEL = os.getenv("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001").strip()
GEMINI_LLM_MODEL = os.getenv("GEMINI_LLM_MODEL", "gemini-3.5-flash-lite").strip()
RETRIEVAL_K = int(os.getenv("RETRIEVAL_K", "4"))
RETRIEVAL_MIN_SCORE = float(os.getenv("RETRIEVAL_MIN_SCORE", "0.15"))
EMBEDDING_BATCH_SIZE = int(os.getenv("EMBEDDING_BATCH_SIZE", "50"))
EMBEDDING_BATCH_DELAY_SECONDS = float(os.getenv("EMBEDDING_BATCH_DELAY_SECONDS", "35"))
CHROMA_DIR = str(VECTORSTORE_DIR / os.getenv("CHROMA_DIR", "chroma_db"))
COLLECTION_NAME = os.getenv("CHROMA_COLLECTION", "properties").strip()
SQLITE_PATH = DB_DIR / os.getenv("SQLITE_DB", "realestate_kb.db")


def resolve_project_path(value: str | os.PathLike[str]) -> Path:
    """Resolve a path relative to the project root unless already absolute."""
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def get_engine():
    """Return a SQLAlchemy engine for the configured SQLite/Postgres backend."""
    if DB_BACKEND == "postgres":
        host = os.getenv("PG_HOST", "localhost")
        port = os.getenv("PG_PORT", "5432")
        db = os.getenv("PG_DB", "realestate_kb")
        user = os.getenv("PG_USER", "postgres")
        password = os.getenv("PG_PASSWORD", "")
        # URL.create safely handles passwords containing @, :, /, etc.
        from sqlalchemy.engine import URL
        url = URL.create(
            "postgresql+psycopg2",
            username=user,
            password=password,
            host=host,
            port=int(port),
            database=db,
        )
        return create_engine(url, pool_pre_ping=True)

    if DB_BACKEND != "sqlite":
        raise ValueError("DB_BACKEND must be either 'sqlite' or 'postgres'.")

    return create_engine(f"sqlite:///{SQLITE_PATH}", pool_pre_ping=True)


def _require_api_key() -> None:
    if not GOOGLE_API_KEY or GOOGLE_API_KEY == "your_google_api_key_here":
        raise RuntimeError(
            "GOOGLE_API_KEY is not configured. Copy .env.example to .env and "
            "set your Google AI Studio API key locally."
        )


def get_embeddings():
    """Return the configured Gemini embedding model for ChromaDB."""
    _require_api_key()
    from langchain_google_genai import GoogleGenerativeAIEmbeddings
    return GoogleGenerativeAIEmbeddings(
        model=GEMINI_EMBEDDING_MODEL,
        google_api_key=GOOGLE_API_KEY,
    )


def get_llm(temperature: float = 0):
    """Return the configured Gemini generation model."""
    _require_api_key()
    from langchain_google_genai import ChatGoogleGenerativeAI
    return ChatGoogleGenerativeAI(
        model=GEMINI_LLM_MODEL,
        temperature=temperature,
        google_api_key=GOOGLE_API_KEY,
    )
