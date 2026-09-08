"""
LLM-as-judge for Task 5's hallucination evaluation. Grades an ACTUAL
generated answer (from generate_answer.py) against the verified context it
was given, rather than only checking whether retrieval found the right
document. This is what closes the gap the reviewer flagged: the previous
version of hallucination_eval.py only tested the retrieval/guardrail layer
and never looked at what Gemini actually said.

Uses structured output (a Pydantic schema via
ChatGoogleGenerativeAI.with_structured_output) so grading returns reliable
booleans instead of free text that would need fragile parsing.

Requires GOOGLE_API_KEY in .env -- makes a real Gemini call per question
graded. Cannot be exercised inside the build sandbox (no network route to
Google's API here); see hallucination_eval.py's docstring for how this was
verified without a live key.
"""
from pydantic import BaseModel, Field
from langchain_core.messages import SystemMessage, HumanMessage
from config import get_llm

JUDGE_SYSTEM_PROMPT = """You are a strict fact-checking auditor for a real
estate assistant. You will be given:
  1. VERIFIED CONTEXT -- the only facts the assistant was allowed to use.
  2. A QUESTION asked by a customer.
  3. The assistant's GENERATED ANSWER.

Your job is to judge the generated answer against the verified context
ONLY -- not against your own knowledge of Pakistani real estate. Decide:

- grounded: true if every factual claim in the answer is directly supported
  by the verified context, OR if the verified context does not contain the
  answer and the assistant correctly said the information is not available.
  false if the answer states or implies any fact not present in the
  verified context.
- hallucinated: true if the answer contains ANY specific fact (a price,
  school name, hospital name, policy detail, percentage, developer name,
  agent name, etc.) that is NOT present in the verified context, even if
  the rest of the answer is otherwise reasonable. false otherwise.
- correct_refusal: true ONLY if the verified context did not contain the
  requested information AND the assistant explicitly said so instead of
  guessing. false if the context did contain the answer (refusal is not
  applicable), or if the context lacked the answer but the assistant
  fabricated one anyway.
- reasoning: one short sentence explaining your judgment.

Be strict: a plausible-sounding number or name that does not literally
appear in the verified context counts as hallucinated, even if it seems
like a reasonable estimate.
"""


class AnswerJudgment(BaseModel):
    grounded: bool = Field(description="Every claim is supported by context, or a correct refusal")
    hallucinated: bool = Field(description="Contains any fact not present in the verified context")
    correct_refusal: bool = Field(description="Context lacked the answer AND the assistant correctly said so")
    reasoning: str = Field(description="One short sentence explaining the judgment")


def judge_answer(question, verified_context, generated_answer):
    """Makes a real Gemini call (via config.get_llm()) to grade a single
    generated answer against its verified context. Returns an
    AnswerJudgment. A fresh LLM instance is used for judging -- the judge
    never sees the generation call's own reasoning, only its final output,
    to avoid the judge just rubber-stamping its own generation trace."""
    llm = get_llm(temperature=0).with_structured_output(AnswerJudgment)
    prompt = (
        f"VERIFIED CONTEXT:\n{verified_context}\n\n"
        f"QUESTION: {question}\n\n"
        f"GENERATED ANSWER: {generated_answer}\n\n"
        f"Judge this answer per the rules in your system instructions."
    )
    result = llm.invoke([SystemMessage(content=JUDGE_SYSTEM_PROMPT), HumanMessage(content=prompt)])
    return result
