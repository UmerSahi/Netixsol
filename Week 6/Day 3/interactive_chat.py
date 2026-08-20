"""
INTERACTIVE AFL TERMINAL
========================
Small CLI wrapper around AFLChatAgent.

Commands:
  /reset  clear conversation context
  /help   show examples
  /exit   quit
"""

from afl_agent import AFLChatAgent


def main() -> None:
    """Run an interactive terminal session."""
    agent = AFLChatAgent()
    print("AFL Assistant ready. Type /help for examples or /exit to quit.")

    while True:
        try:
            text = input("\nYou > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            return

        if not text:
            continue
        if text.lower() in {"/exit", "/quit", "exit", "quit"}:
            print("Goodbye!")
            return
        if text.lower() == "/reset":
            agent.reset()
            print("Conversation reset.")
            continue
        if text.lower() == "/help":
            print(
                "Examples:\n"
                "  - How did Collingwood perform in 2023?\n"
                "  - How many disposals did Nick Daicos have in 2023?\n"
                "  - Show Geelong's last 5 matches in 2024.\n"
                "  - Compare Collingwood and Melbourne head-to-head.\n"
                "  - Who led the AFL in goals in 2023?"
            )
            continue

        result = agent.chat(text)
        print(f"\nAgent > {result['answer']}")
        print(f"[Tools] {', '.join(result['tools_called']) or 'none'}")
        print(f"[Grounding] {result['grounding_report']['verdict']}")


if __name__ == "__main__":
    main()
