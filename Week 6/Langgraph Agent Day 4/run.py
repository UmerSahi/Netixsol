"""
run.py
======
Interactive AFL chatbot. Run: python run.py

No configuration needed -- paths auto-detect (see train.py). If you have a
.env file with GOOGLE_API_KEY set, it will be loaded automatically (falls
back silently to the deterministic router if python-dotenv or the key
isn't available).
"""
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # optional dependency -- GOOGLE_API_KEY can still be set another way

from graph import ask

if __name__ == "__main__":
    thread_id = "interactive-session"
    print("AFL AI Agent ready. Type 'quit' to exit, 'new' to reset conversation context.")
    print("Examples: 'Who will win Cats vs Pies?' | 'Who will win AFL in 2030?' | "
          "'What was Patrick Dangerfield's average disposals in 2020?'\n")
    while True:
        query = input("Ask an AFL question: ")
        if query.strip().lower() in {"quit", "exit", "q"}:
            break
        if query.strip().lower() == "new":
            thread_id = thread_id + "-new"
            print(f"Started a new conversation (context reset).")
            continue
        result = ask(query, thread_id=thread_id)
        print(result["final_response"])
        print()
