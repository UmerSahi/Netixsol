# Running on Postgres

Everything now reads its connection details from a single `.env` file (see
`.env.example` in the project root) via `config.py` -- you set it up once
and never `export` anything in the terminal again.

## 1. Install dependencies
```bash
pip install pandas sqlalchemy psycopg2-binary python-dotenv google-genai
```

## 2. Create the database
```bash
createdb realestate_kb
```

## 3. Fill in .env (project root, one level up from this folder)
```
DB_BACKEND=postgres
PG_HOST=localhost
PG_PORT=5432
PG_DB=realestate_kb
PG_USER=postgres
PG_PASSWORD=yourpassword
```

## 4. Load the data
```bash
cd ..                          # project root, where the CSVs live
python build_knowledge_base.py Property_with_Feature_Engineering.csv
```
`build_knowledge_base.py` reads `config.py`, which reads `.env` -- so once
`DB_BACKEND=postgres` is set, this same script loads straight into Postgres
instead of SQLite. Nothing else changes.

## 5. Run any script as normal
```bash
python structured_retrieval.py
python recommendation_engine.py
python generate_answer.py
```
All of them call `config.get_engine()`, so they follow whatever `.env` says.

## Switching back to SQLite
Set `DB_BACKEND=sqlite` in `.env` (or delete the line -- sqlite is the
default). No other change needed.
