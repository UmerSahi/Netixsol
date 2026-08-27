"""
Offline test for hallucination_eval.py's harness logic (SQL context
building, guardrail short-circuiting, metric computation) using stub
generate/judge/retrieve functions -- so the plumbing can be verified
without a live GOOGLE_API_KEY or network access.

This does NOT replace running hallucination_eval.py for real -- it only
proves the harness computes the right thing given known inputs. Run it any
time you change run_eval()/build_verified_context()'s logic.

    python test_hallucination_eval.py
"""
from hallucination_eval import run_eval, test_cases


class StubJudgment:
    def __init__(self, grounded, hallucinated, correct_refusal):
        self.grounded = grounded
        self.hallucinated = hallucinated
        self.correct_refusal = correct_refusal
        self.reasoning = "stub"


def stub_generate(context, question):
    """Simulates a perfectly faithful model: refuses when context has no
    evidence, otherwise echoes the context (never fabricates)."""
    if "no matching rows found" in context or "no relevant information retrieved" in context:
        return "Maazrat, mujhe verified data mein iska jawab nahi mila."
    return f"[answer grounded in context]: {context[:60]}..."


def stub_judge(question, context, answer):
    """A simple, deterministic stand-in for the LLM judge: since
    stub_generate is faithful by construction, grade purely on whether
    evidence existed and whether the stub correctly refused."""
    no_evidence = "no matching rows found" in context or "no relevant information retrieved" in context
    refused = "nahi mila" in answer
    if no_evidence:
        return StubJudgment(grounded=refused, hallucinated=not refused, correct_refusal=refused)
    return StubJudgment(grounded=True, hallucinated=False, correct_refusal=False)


def stub_vector_retrieve(question, source_type_filter):
    """Stands in for real semantic retrieval -- grounded iff the question
    is one of the 15 designed to be answerable AND uses vector mode."""
    tc = next(t for t in test_cases if t["q"] == question)
    if tc["expect_grounded"]:
        return {"grounded": True, "chunks": [{"score": 0.8, "text": f"stub evidence for: {question}"}],
                "context_for_llm": f"stub evidence for: {question}"}
    return {"grounded": False, "chunks": [], "context_for_llm": ""}


def test_pass1_no_guardrails():
    df, metrics = run_eval(with_guardrails=False, _generate_fn=stub_generate,
                            _judge_fn=stub_judge, _vector_retrieve_fn=stub_vector_retrieve)
    assert len(df) == 20, f"expected 20 rows, got {len(df)}"
    # With a perfectly faithful stub model, retrieval accuracy, grounding,
    # and hallucination should all be perfect / zero.
    assert metrics["retrieval_accuracy"] == 1.0, metrics
    assert metrics["grounding_rate"] == 1.0, metrics
    assert metrics["hallucination_rate"] == 0.0, metrics
    assert metrics["correct_refusal_rate"] == 1.0, metrics
    print("test_pass1_no_guardrails: PASS")
    return df, metrics


def test_pass2_with_guardrails():
    df, metrics = run_eval(with_guardrails=True, _generate_fn=stub_generate,
                            _judge_fn=stub_judge, _vector_retrieve_fn=stub_vector_retrieve)
    assert len(df) == 20
    n_blocked = df["blocked_by_guardrail"].notna().sum()
    assert n_blocked >= 3, f"expected at least the 3 keyword-triggerable adversarial Qs blocked, got {n_blocked}"
    assert metrics["hallucination_rate"] == 0.0, metrics
    print(f"test_pass2_with_guardrails: PASS ({n_blocked} questions blocked pre-generation)")
    return df, metrics


def test_hallucinating_model_is_caught():
    """Sanity check the other direction: if the model fabricates when there
    is no evidence, the harness must catch it (hallucination_rate > 0)."""
    def lying_generate(context, question):
        return "Yeh property PKR 5 crore ki hai aur is mein swimming pool hai."  # always fabricates

    def honest_judge(question, context, answer):
        no_evidence = "no matching rows found" in context or "no relevant information retrieved" in context
        if no_evidence:
            return StubJudgment(grounded=False, hallucinated=True, correct_refusal=False)
        return StubJudgment(grounded=True, hallucinated=False, correct_refusal=False)

    df, metrics = run_eval(with_guardrails=False, _generate_fn=lying_generate,
                            _judge_fn=honest_judge, _vector_retrieve_fn=stub_vector_retrieve)
    assert metrics["hallucination_rate"] > 0, "harness failed to catch a fabricating model"
    print(f"test_hallucinating_model_is_caught: PASS (hallucination_rate={metrics['hallucination_rate']:.0%})")


if __name__ == "__main__":
    test_pass1_no_guardrails()
    test_pass2_with_guardrails()
    test_hallucinating_model_is_caught()
    print("\nAll harness tests passed -- run_eval()'s SQL context building, guardrail")
    print("short-circuit, and metric computation are correct. Swap in the real")
    print("generate_answer_from_context/judge_answer (the defaults) with a real")
    print("GOOGLE_API_KEY for the actual evaluation numbers.")
