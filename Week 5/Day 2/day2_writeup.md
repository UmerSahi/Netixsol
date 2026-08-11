# Day 2 Write-Up — Raw Python vs. LangChain Agent

## Concept Mapping

| Day 1 (raw Python) | LangChain equivalent |
|---|---|
| `client.models.generate_content(...)` | `ChatGoogleGenerativeAI` -- an **LLM wrapper** with a standard `Runnable` interface (`.invoke`/`.stream`/`.batch`) shared across every provider LangChain supports |
| `TOOL_SCHEMAS` dict + `TOOL_EXECUTORS` dict, kept in sync by hand | A single `@tool`-decorated function -- schema is derived automatically from type hints and the docstring |
| `run_agent()` -- hand-written `while` loop | `create_tool_calling_agent(...)` + `AgentExecutor` -- Reason/Act/Observe loop, `max_iterations` guardrail, and tool dispatch built in |
| `contents` list threaded through every call by hand | `RunnableWithMessageHistory` wrapping a per-session `InMemoryChatMessageHistory` -- LangChain manages the list and re-injects it via `chat_history` on every call, once its output-format assumption is satisfied (see below) |

## LCEL and the Pipe Operator

Every LangChain component is a `Runnable` with a standard `.invoke()` interface. `|` is
`Runnable.__or__`: it builds a `RunnableSequence` that feeds the left side's output
into the right side's input. `prompt | llm | parser` is exactly
`parser.invoke(llm.invoke(prompt.invoke(input)))`, expressed as a pipeline instead of
nested calls -- and because every piece shares the same interface, the whole chain
gets streaming, batching, and async support for free.

## Annotated Reasoning Trace (Task 3's multi-step run)

```
> Entering new AgentExecutor chain...

Invoking: `lookup_product_price` with `{'product_name': 'UltraBook Air 13'}`
```
**^ Act.** (The Reason step that produced this decision is not printed by default --
only its result, the tool call, is visible.)

```
{"success": true, "matches": [{"name": "UltraBook Air 13", "price_usd": 899.0, ...}]}
```
**^ Observe.** Tool output fed back to the model automatically.

```
Invoking: `lookup_product_price` with `{'product_name': 'UltraBook Pro 14'}`
```
**^ Act** again -- the model decided (silently) it still needed the second product's price.

```
{"success": true, "matches": [{"name": "UltraBook Pro 14", "price_usd": 1499.0, ...}]}
```
**^ Observe** again.

```
[{'type': 'text', 'text': 'Here is the price comparison...UltraBook Air 13.',
  'index': 0, 'extras': {'signature': 'EjQKMgERTTIP...'}}]

> Finished chain.
```
**^ Final Reason → answer.** Plain text with no further tool call is `AgentExecutor`'s
stopping condition -- the same check Day 1's loop made explicitly (`if response has
no function calls: return`). Note the actual shape here: Gemini 3.5 returned the
answer as a **list of content blocks** carrying a `thought_signature` in `extras`, not
a plain string. This is real output from the live run, not a simplification -- it's
the same signature machinery discussed below, just visible in the final answer
instead of only in intermediate tool calls. Every place the notebook displays or
stores this text runs it through a small `extract_text()` helper first to strip it
back down to a plain string.

### What's similar to Day 1's raw log
The loop shape is identical: reason, act, observe, repeat, stop on plain text. Tool
calls and their JSON results are equally visible in both.

### What's hidden now
Day 1's `[reason]` lines printed the model's own words *between* tool calls;
`AgentExecutor`'s default verbose output only shows intermediate text when the model
happens to attach it to the same message as a tool call. Also hidden: the exact
provider-specific message format `ChatPromptTemplate` assembles under the hood, and
-- critically for Gemini 3.5 -- the `thought_signature` bookkeeping the API requires
on every multi-turn tool call. Day 1 had to be patched by hand to preserve that
signature; `create_tool_calling_agent` handles the *tool-call* side of this correctly
out of the box, because its scratchpad formatter reuses the model's exact returned
`AIMessage` object rather than rebuilding one. The *memory* side of the same
signature format is a different story -- see below.

## A Real Bug: `RunnableWithMessageHistory` vs. Gemini 3.5's Content Blocks

This is the most useful failure mode from the whole exercise, because it wasn't
manufactured -- it broke a live run.

The original Task 4 plan was `RunnableWithMessageHistory` wrapping `agent_executor`,
exactly as LangChain's own docs recommend. It worked for Turn 1 (no prior history to
re-inject yet), then crashed on Turn 2 with:

```
ValueError: Message dict must contain 'role' and 'content' keys, got
{'type': 'text', 'text': 'The price of the UltraBook Air 13 is $899.',
 'index': 0, 'extras': {'signature': 'EjQKMgERTTIP...'}}
```

**Root cause:** `RunnableWithMessageHistory` saves whatever comes back from
`result["output"]` into history. It expects that value to be either a plain string
or a list of already-built messages. Gemini 3.5 returned a *list of content blocks*
instead -- each block a dict with `type`/`text`/`extras`, not `role`/`content`.
`RunnableWithMessageHistory` misread that list as "a list of messages to save" and
tried to convert each block into a message on its own, which failed immediately since
a content block has none of the fields a message needs.

**Fix:** stay on the modern API instead of abandoning it -- fix the mismatch at its
source. Compose a small `RunnableLambda(normalize_agent_output)` *between*
`agent_executor` and `RunnableWithMessageHistory`, so the wrapper never receives
Gemini's list of content blocks in the first place; it only ever sees a plain
string, which is the shape it expects and already knows how to save as a normal
`AIMessage`. With that one normalization step in the pipeline,
`RunnableWithMessageHistory` runs exactly as advertised across all three turns --
no manual list-threading, no `ConversationBufferMemory` fallback, no reimplementing
Day 1's approach. The lesson isn't "the modern wrapper is unreliable" -- it's that
`RunnableWithMessageHistory`'s save-to-history step is *not provider-aware* and
assumes every model's output already arrives in a canonical shape. Gemini 3.5
doesn't guarantee that, so the adapter step has to live somewhere; putting it in the
chain itself (rather than in a hand-rolled memory function) keeps LangChain doing
the actual memory management instead of us.

## Failure Mode: Tool Exception Handling

A raw Python exception inside a `@tool` function propagates all the way up and
crashes `AgentExecutor.invoke()` -- LangChain only auto-catches its own
`ToolException` type, not arbitrary exceptions. The fix requires two things: the tool
must catch its real error and re-raise as `ToolException`, and `handle_tool_error =
True` must be set on the tool object. With that in place, a simulated flaky lookup
tool failed on its first call, the exception message was fed back to the model as a
normal observation, and the model retried the same tool on its next turn -- which
succeeded, producing a clean final answer with no custom retry code written.

This is the direct LangChain analogue of Day 1's rule that every tool executor return
a `{"success": False, "error": ...}` dict instead of raising: LangChain's version is
opt-in per tool via `handle_tool_error` rather than mandatory by convention, which
means it's easier to forget precisely because the framework otherwise *looks* like
it's handling errors for you everywhere.

## What LangChain Made Easier, and Where the Abstraction Leaks

LangChain removed genuine boilerplate: tool schemas come from type hints and
docstrings instead of being written twice, and the ReAct loop plus its iteration
guardrail live inside `AgentExecutor` instead of a hand-written `while` loop. Memory
is the clearest example of *leaky* abstraction in this whole exercise: the "modern,"
recommended `RunnableWithMessageHistory` wrapper broke on real model output the
moment a second turn needed to save history, because it silently assumes every
model's output arrives as a plain string. The fix stayed inside LangChain's own
composition model -- inserting a one-line `RunnableLambda` to normalize the output
before `RunnableWithMessageHistory` ever sees it -- but finding that fix required
understanding exactly what shape the wrapper expects and why Gemini didn't provide
it by default, which the framework doesn't surface on its own. Beyond that: the exact
constructors this task uses (`create_tool_calling_agent`, `AgentExecutor`,
`ConversationBufferMemory`) were split out of core `langchain` into a separate
`langchain-classic` package in favor of a LangGraph-based `create_agent`, so "the
standard way to build an agent" is a moving target across versions. And tool error
handling still requires the same underlying discipline Day 1 required -- catching
your own exceptions -- just easier to overlook because the framework looks like it's
handling it for you everywhere else. The net effect: LangChain removed real
boilerplate on the tool-definition and loop-control side, but added a new surface
area of provider-specific quirks and version churn that Day 1's raw code, being
simpler, never had to contend with.
