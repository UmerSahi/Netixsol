"""
Single config module -- loads .env once and exposes everything the other
scripts need: the DB engine, the Gemini embedding model, and the Gemini
chat model. Every other script imports from here, so you configure things
in exactly one place (.env) and never need `export` in the terminal.
"""
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine

load_dotenv()  # reads .env in the current working directory, if present

DB_BACKEND = os.getenv("DB_BACKEND", "postgres")  # "postgres" or "sqlite"
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
GEMINI_EMBEDDING_MODEL = os.getenv("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001")
GEMINI_LLM_MODEL = os.getenv("GEMINI_LLM_MODEL", "gemini-3.5-flash-lite")


def get_engine():
    """Returns a SQLAlchemy engine for Postgres or SQLite, per DB_BACKEND in .env."""
    if DB_BACKEND == "postgres":
        host = os.getenv("PG_HOST", "localhost")
        port = os.getenv("PG_PORT", "5432")
        db = os.getenv("PG_DB", "realestate_kb")
        user = os.getenv("PG_USER", "postgres")
        pwd = os.getenv("PG_PASSWORD", "sahi")
        url = f"postgresql+psycopg2://{user}:{pwd}@{host}:{port}/{db}"
    else:
        url = "sqlite:///realestate_kb.db"
    return create_engine(url)


def _require_api_key():
    if not GOOGLE_API_KEY or GOOGLE_API_KEY == "your_google_api_key_here":
        raise RuntimeError(
            "GOOGLE_API_KEY is not set. Copy .env.example to .env and fill in "
            "your real Google API key (get one at https://aistudio.google.com/apikey)."
        )


def get_embeddings():
    """Returns the Gemini EMBEDDING model (gemini-embedding-001 by default).
    This is intentionally a separate model/object from get_llm() below --
    embeddings and generation must never share a call."""
    _require_api_key()
    from langchain_google_genai import GoogleGenerativeAIEmbeddings
    return GoogleGenerativeAIEmbeddings(model=GEMINI_EMBEDDING_MODEL)


def get_llm(temperature=0):
    """Returns the Gemini GENERATION model (gemini-3.5-flash-lite by
    default). temperature=0 by default -- this is a property-information
    system where factual consistency matters more than creativity."""
    _require_api_key()
    from langchain_google_genai import ChatGoogleGenerativeAI
    return ChatGoogleGenerativeAI(model=GEMINI_LLM_MODEL, temperature=temperature)
