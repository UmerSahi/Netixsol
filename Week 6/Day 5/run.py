"""
run.py
======
Interactive AFL chatbot. Run: python run.py

No configuration needed -- paths auto-detect (see train.py). If you have a
.env file with GOOGLE_API_KEY set, it will be loaded automatically (falls
back silently to the deterministic router if python-dotenv or the key
isn't available). Type 'debug' to toggle showing which router
(gemini/rule_based) and latency answered each turn -- useful for
confirming an attached API key is actually being used (see README.md).
"""
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # optional dependency -- GOOGLE_API_KEY can still be set another way

import os
from graph import ask

if __name__ == "__main__":
    thread_id = "interactive-session"
    debug = False
    key_status = "GOOGLE_API_KEY detected -- Gemini router active (falls back to rules on any error)" \
        if os.environ.get("GOOGLE_API_KEY") else "No GOOGLE_API_KEY set -- using the deterministic rule-based router"
    print("AFL AI Agent ready.", key_status)
    print("Type 'quit' to exit, 'new' to reset conversation context, 'debug' to toggle router/latency info.")
    print("Examples: 'Who will win Cats vs Pies?' | 'Who will win AFL in 2030?' | "
          "'What was Patrick Dangerfield's average disposals in 2020?' | "
          "'Sam Walsh vs Lachie Neale disposals in 2023'\n")
    while True:
        query = input("Ask an AFL question: ")
        if query.strip().lower() in {"quit", "exit", "q"}:
            break
        if query.strip().lower() == "new":
            thread_id = thread_id + "-new"
            print(f"Started a new conversation (context reset).")
            continue
        if query.strip().lower() == "debug":
            debug = not debug
            print(f"Debug info {'on' if debug else 'off'}.")
            continue
        result = ask(query, thread_id=thread_id)
        print(result["final_response"])
        if debug:
            print(f"  [intent={result.get('intent')} router={result.get('router_source')} "
                  f"latency={result.get('latency_ms')}ms validation={result.get('validation_status')}]")
        print()
