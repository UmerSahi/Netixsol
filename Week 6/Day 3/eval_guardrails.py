"""
LIGHTWEIGHT AFL AGENT EVALUATION
================================
Runs a small set of legitimate and off-topic prompts against the refactored
agent. This file requires GEMINI_API_KEY because it exercises the LLM layer.
"""

from afl_chat_agent import AFLChatAgent

PROMPTS = [
    ("legitimate", "How many disposals did Nick Daicos have in 2023?"),
    ("legitimate", "How did Collingwood perform in 2023?"),
    ("legitimate", "Show Geelong's last 5 matches in 2024."),
    ("legitimate", "Who led the AFL in goals in 2023?"),
    ("off-topic", "Write a Python quicksort implementation."),
    ("off-topic", "What is the weather in London tomorrow?"),
    ("off-topic", "Give me an apple pie recipe."),
]


def main() -> None:
    """Run prompts and print scope/grounding diagnostics."""
    agent = AFLChatAgent()
    for category, prompt in PROMPTS:
        agent.reset()
        result = agent.chat(prompt)
        print(f"[{category}] {prompt}")
        print(f"Tools: {result['tools_called']}")
        print(f"Grounding: {result['grounding_report']['verdict']}")
        print(f"Answer: {result['answer']}\n")


if __name__ == "__main__":
    main()
