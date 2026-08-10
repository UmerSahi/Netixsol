# Agent Foundations — Write-Up
### Week 5, Day 1 — Reasoning Loops, Tool Calling & Raw Python Agents

## The ReAct Loop

An agent, as built here, is nothing more than an LLM placed inside a loop with tools:
**Reason → Act → Observe → repeat**, until the model decides it has enough information
and returns plain text instead of a tool call. Concretely:

```
contents = [user_task]
loop up to max_iterations:
    response = model(contents, tools)           # Reason (and possibly Act)
    if response has no function_call parts:
        return response.text                    # done
    for each function_call in response:
        result = execute(function_call)         # Act
        contents.append(call, result)           # Observe -> fed back as memory
```

What separates this from a chatbot is that no code decides *what happens next* — the
model does, turn by turn, based on what it has observed so far. What separates it from
a fixed workflow is that the number and order of tool calls isn't known in advance; it
emerges from the task. A `max_iterations` cap is the one safeguard the surrounding code
imposes, purely to bound runaway behavior.

An agent is overkill whenever the sequence of steps is already known — a single
well-crafted prompt or a short deterministic script will be faster, cheaper, and far
more predictable than paying for a loop of model calls to reinvent a plan that never
changes.

## Tool Schemas Used

Three tools were defined, each with a `name`, a `description`, and a JSON-Schema
`input_schema` (converted into a Gemini `FunctionDeclaration` via the `google-genai`
SDK):

- **`calculator`** — evaluates a restricted arithmetic expression (parsed via `ast`,
  only `+ - * / % **` allowed, no `eval()`), returns `{result}` or `{error}`.
- **`get_weather`** — looks up a city in a small demo dataset, returns
  `{temp_c, condition}` or `{error}` for cities outside the dataset.
- **`read_text_file`** — reads a local file by name, returns `{content}` or `{error}`.

The `description` field is the *entire* specification the model has of what a tool
does and when to use it — it never sees the implementation. A vague description
("does math") invites wrong-tool or wrong-argument calls; a precise one (what it does,
when to use it, an example input) is the single biggest lever for reliable tool
selection, more so than any change to the surrounding prompt.

## Multi-Step Test

Task: *"Look up the weather in Tokyo and Paris and tell me which is warmer."*
The agent correctly called `get_weather` twice (once per city) across two loop
iterations, then answered directly on the third iteration without any further tool
call — the comparison itself required no tool, only reasoning over two prior
observations already sitting in conversation memory.

## Memory: Two Kinds

**Conversation memory** is the literal `contents` list replayed to the model every
turn — the model has no memory beyond what is physically present in that list.
**Working memory** is state the surrounding code tracks *outside* that list for its
own purposes — in this build, a `trace` list logging every reasoning snippet, tool
call, and observation independent of the API's message format. Verbose per-step
logging (`[reason] / [act] / [observe]`) is this scratchpad made visible, and is the
first thing to reach for when debugging any agent framework going forward.

## Failure Modes Observed & Mitigations

| Failure mode | Mitigation |
|---|---|
| Ambiguous instructions | Prompt permits asking for clarification instead of guessing |
| Tool returns an error mid-task | Executors return an error *dict* (`{"success": False, "error": ...}`), never raise — the model gets a normal `function_response` to reason around |
| Task needs an undefined tool | System prompt forbids inventing tools; model reports it can't complete the task |
| Hallucinated tool call (name not in `TOOL_SCHEMAS`) | Loop does `TOOL_EXECUTORS.get(name)`, returns a structured "unknown tool" error instead of `KeyError`-crashing |
| Infinite/runaway loop | Hard `max_iterations` cap; loop exits with a labelled guardrail message rather than looping forever |
| Wrong tool arguments | Sandboxed `ast`-based parser catches malformed expressions; error is fed back so the model can retry |
| Silent/swallowed exceptions | Every tool call is wrapped in `try/except` inside the loop, so a crash becomes a visible logged error, never a dead process |

A well-behaved model rarely volunteers a call to a tool name it was never given, so the
hallucinated-tool-call row above was verified directly rather than by hoping to
provoke it through prompting: a fake `function_call` for an unregistered tool name was
fed straight into the same `TOOL_EXECUTORS.get(name)` guardrail used inside the loop,
confirming it returns a structured `"unknown tool"` error instead of raising
`KeyError` and killing the run. The "undefined tool" row was tested behaviorally
instead — asking the agent to do something no tool covers (e.g. sending a text
message) — where the expected and observed outcome was the model declining in plain
text rather than inventing a tool call at all.

## Why Do Frameworks Exist?

Everything above is a few hundred lines of code — but it's boilerplate that would
otherwise be re-derived and re-tested on every project: consistent message formats
across model providers, standardized retry/error handling, graph-based control flow
for genuinely branching agents (parallel calls, sub-agents, human-in-the-loop
interrupts), streaming, cross-session memory, and observability tooling. None of it is
conceptually different from the loop built here — it's still Reason → Act → Observe —
but having built the raw version first makes it clear what a framework is doing under
its abstractions, which is what makes debugging one tractable instead of magic.
