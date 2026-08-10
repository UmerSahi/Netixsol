# Task 1 — Agent Concepts & Mental Model

## Agent vs. Chatbot vs. Workflow

**Chatbot** — a single request/response text exchange. It reasons in one shot and
produces an answer; it has no ability to *do* anything in the world beyond generating
text, and no loop: one turn in, one turn out.

**Workflow (a.k.a. "pipeline")** — a fixed, pre-written sequence of steps (possibly
calling an LLM at one or more steps) where the *order and branching* is decided by the
programmer ahead of time. For example: "call the summarizer, then call the classifier,
then call the formatter." The LLM fills in content, but never decides *what happens
next* — that control flow is hardcoded outside the model.

**Agent** — an LLM that is put in a loop and given tools, and *the model itself
decides, turn by turn, what to do next*: which tool to call, with what arguments,
whether it has enough information yet, and when to stop. The control flow lives inside
the model's reasoning, not in the surrounding code.

## What Makes Something "Agentic"?

- **Autonomy** — the next action is chosen by the model, not hardcoded by the
  developer.
- **Tool use** — the model can act on the world (query data, run code, call an API)
  and observe the result, not just emit text.
- **Multi-step planning** — it can decompose a goal into an unknown-in-advance number
  of intermediate actions.
- **Self-correction** — it can notice a tool failed or an assumption was wrong, and
  adjust its next action instead of just crashing or hallucinating past the error.

A system doesn't need all four in equal measure to count as agentic, but the more of
these it exhibits, the further it sits from a chatbot or a fixed workflow.

## The ReAct Pattern (Reason → Act → Observe → repeat)

```
Reason:   "The user wants weather for two cities. I have neither yet.
           I should look up the first city."
Act:      call_tool(get_weather, {"city": "Tokyo"})
Observe:  {"temp_c": 31, "condition": "humid"}
Reason:   "I have Tokyo. Still need Paris."
Act:      call_tool(get_weather, {"city": "Paris"})
Observe:  {"temp_c": 22, "condition": "clear"}
Reason:   "I now have both temperatures. I can compare them directly —
           no more tools needed."
Answer:   "Tokyo (31°C) is warmer than Paris (22°C)."
```

Pseudocode:

```
contents = [user_task]
loop up to max_iterations:
    response = model(contents, tools)
    if response has no function calls:
        return response.text                 # done
    for each function_call in response:
        result = execute(function_call)      # Act
        contents.append(call, result)        # Observe, fed back in
    # implicit Reason happens on the *next* model call, conditioned
    # on everything observed so far
```

Each loop turn is one Reason step (deciding what to do, visible or not), followed by
an Act (a tool call) and an Observe (the tool's result fed back into the conversation
so the next Reason step has it available). The loop ends the moment the model responds
with plain text instead of a tool call.

## When Is an Agent Overkill?

If the number of steps and their order is already known ahead of time, a fixed script
or a single well-crafted prompt is faster, cheaper, and far more predictable than a
loop — every extra model call adds latency, cost, and a new chance to go off the
rails. Reach for an agent only when the *path itself* is unknown in advance — the
number or order of actions depends on intermediate results — not just because tools
happen to be involved.
