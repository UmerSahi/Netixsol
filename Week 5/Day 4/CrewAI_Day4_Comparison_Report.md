# CrewAI Multi Agent Comparison Report
### Sequential vs Hierarchical vs Single Agent (Day 3 LangGraph)

Task: Given a client's laptop shortlist (UltraBook Air 13 vs UltraBook Pro 14), research specs and pricing, analyze the trade offs, and produce a recommendation the client can act on right away. This is the same underlying job Day 3's single LangGraph agent solved end to end, split here across a CrewAI crew of specialists, first in Process.sequential, then in Process.hierarchical.

---

## 1. Crew Design

Three roles, each owning a distinct stage and a distinct failure mode to guard against, with no overlap.

| Agent | Role | Goal | Backstory |
|---|---|---|---|
| Product Researcher | Laptop Product Researcher | Retrieve accurate, current specs and pricing for every candidate laptop, nothing interpreted yet. | Former retail electronics buyer, obsessive about sourcing every number, never editorializes about which product is better. |
| Pricing and Value Analyst | Pricing and Value Analyst | Turn raw specs into computed comparisons: price delta, price per GB of RAM, trade offs. | Former financial analyst, thinks in ratios and deltas, distrusts any comparison not backed by an actual calculation. |
| Recommendation Writer | Client Facing Recommendation Writer | Turn the analyst's numbers into a short, plain language recommendation the client can act on. | Client facing consultant, cuts jargon, always closes with one explicit recommendation. |

Tool assignment was kept role appropriate rather than giving every agent every tool:

| Agent | Tools | Why |
|---|---|---|
| Researcher | lookup_product_price, catalog_file | Its job is retrieval only, no calculator, so it cannot sneak analysis into raw data. |
| Analyst | calculator | Computes on data the researcher already retrieved (passed in via task context), does not need catalog access. |
| Writer | (none) | Pure synthesis of the analyst's numbers, tools would let it re fetch or re compute instead of faithfully reporting upstream work. |

Why a crew, and where it is not worth it: splitting the work lets each stage be penalized for a different failure mode, invented numbers for the researcher, arithmetic mistakes for the analyst, buried recommendations for the writer, instead of one generalist blending all three concerns at once. It also makes each stage independently inspectable and swappable. Where it is not worth it: for a task this small, four laptops, one arithmetic fact, one paragraph of prose, a single well designed agent can do all three steps correctly in one pass, and the crew adds real latency and token cost for a quality gain that is marginal at this scale.

---

## 2. Sequential Run

Tasks were wired with context, which injects each referenced task's output directly into the next task's prompt. This is CrewAI's mechanism for task dependencies. The captured run:

```
[Laptop Product Researcher] Final Answer:
Name: UltraBook Air 13   Price: $899  RAM: 8GB  Storage: 256GB  Rating: 4.3
Name: UltraBook Pro 14   Price: $1499 RAM: 16GB Storage: 512GB Rating: 4.6

[Pricing and Value Analyst] Final Answer:
Price delta: $600.00
UltraBook Air 13 price per GB RAM: $112.375   UltraBook Pro 14 price per GB RAM: $93.6875
Trade off: the extra $600 buys double the RAM, double the storage, and a
slightly higher rating, at a lower cost per GB of RAM despite the higher price.

[Client Facing Recommendation Writer] Final Answer:
For your daily routine of email, browsing, and light office work, paying an
extra $600 for the Pro 14 does not make financial sense. The Air 13 handles
these tasks easily while saving significant money, and its 4.3 out of 5 rating
confirms it is a reliable, well liked machine.
Buy the UltraBook Air 13.
```

A format mismatch that had to be fixed: the first draft of the researcher's expected_output just said to report the specs. Left open, the researcher sometimes replied in a conversational paragraph with the price appearing before or after the name and RAM written as 8 gigs instead of a plain number, which the analyst parsed inconsistently and occasionally computed the delta in the wrong direction. The fix was tightening expected_output to a strict key value block per laptop and explicitly telling the researcher not to compute or summarize. Pinning the shape of the hand off, not just its content, is what made downstream parsing reliable.

---

## 3. Hierarchical Run

A fourth agent, a Recommendation Delivery Manager, sits above the same three specialists. Tasks are no longer pre assigned to an agent; the manager delegates each one at runtime through delegate_work_to_coworker calls and can review or re route work before it moves downstream.

On this run, the manager's own delegation choices matched the fixed pipeline order, and every specialist's first attempt was already correct, so there was nothing for the manager to catch or redo. The final recommendation matched the sequential run's quality and even cited an extra computed figure, the $93.69 per GB analyst number, in its closing summary.

---

## 4. Sequential vs Hierarchical

| | Quality | Latency and Token Cost | Reliability |
|---|---|---|---|
| Sequential | Same final answer quality. The pipeline order is fixed and unambiguous, so there is nothing for a manager to improve. | Lower: 21 successful calls, 13,608 tokens, one round per agent, no delegation round trips. | High: deterministic order every run, the only failure mode is a single agent's own output. |
| Hierarchical | Same quality on this run. The manager did not need to redo anything. | Higher: 51 successful calls, 36,270 tokens, about 2.4 times the calls and 2.7 times the tokens of sequential, from the manager's own reasoning plus its delegation calls on top of the same 3 worker calls. | Slightly lower on paper, one more agent whose choices can go wrong, but the review step can catch a bad hand off sequential would ship as is. |

| | Pros | Cons | When to use |
|---|---|---|---|
| Sequential | Cheap, fast, fully deterministic, easy to debug from one linear log | No error recovery, a subtly wrong output just flows downstream | Stage order is fixed and known ahead of time, as with this task |
| Hierarchical | Manager can catch bad hand offs, redelegate, handle unclear agent assignment | More tokens, more latency, one more point of failure in the manager itself | Real delegation ambiguity, need for review between stages, or steps not fixed in advance |

---

## 5. Cost and Token Log

| Run | Successful calls | Tokens | Approx cost |
|---|---|---|---|
| Sequential crew | 21 | 13,608 | about $0.00175 |
| Hierarchical crew | 51 | 36,270 | about $0.00496 |
| Day 3 LangGraph, single agent, reference | 2, generate plus critique, on a clean pass | lowest of the three, no separate hand off overhead | not separately captured |

Day 3's single agent solves the same problem in fewer LLM calls than even the sequential crew, since there is no researcher to analyst to writer hand off overhead to pay for.

---

## 6. Evaluation

Success criteria, scored 1 to 5:
1. Factual grounding: every number traces back to an actual tool call, nothing invented or rounded.
2. Completeness: both laptops named, price delta stated, exactly one explicit recommendation, no it depends hedging.
3. Tone: plain, client appropriate language, no leaked reasoning or agent role play.

| Run | Grounding | Completeness | Tone | Notes |
|---|---|---|---|---|
| Sequential | 5 | 5 | 5 | The $600 delta and 4.3 rating trace directly to tool calls, both laptops are named, closes with Buy the UltraBook Air 13. |
| Hierarchical | 5 | 5 | 5 | Same grounding, plus it cites the $93.69 per GB figure the analyst computed, equally clean client facing tone. |

Only two runs were captured, one per process, since each consumes real API quota, fewer than an ideal three run evaluation would use.

---

## 7. Was the Crew Worth It

Both processes produced an equally well grounded, equally complete, equally well toned recommendation, so adding the manager bought no quality gain here. What differed was pure overhead: hierarchical used about 2.7 times the tokens and 2.4 times the calls of sequential for a result judged identical on all three criteria. Day 3's single LangGraph agent solves the same problem in even fewer calls than the sequential crew, with a simpler, single log debugging story. For a task this small, four laptops, one price delta, one paragraph of prose, a multi agent crew is not worth its added cost, and hierarchical delegation is worth it even less. That balance would likely shift once the task has genuinely separable work at scale, more products to research in parallel, or a real need for a manager to catch a bad hand off before a client sees it, neither of which this four laptop comparison actually has.

---

## 8. Final Comparison, All Three Approaches

| | Day 3 StateGraph, single agent | CrewAI Sequential | CrewAI Hierarchical |
|---|---|---|---|
| Agents | 1, plans, retrieves, generates, and critiques itself | 3 specialists, fixed order | 3 specialists plus 1 manager |
| Control flow | Explicit graph with a real self correction loop, critique back to generate | Fixed linear pipeline, no loop back | Manager can re delegate, closest thing to a loop back |
| LLM calls, this run | 2, generate plus critique, on a clean pass | 21, measured | 51, measured, about 2.4 times sequential |
| Token cost, this run | Lowest, no hand off overhead | 13,608 tokens, about $0.00175 | 36,270 tokens, about $0.00496, about 2.7 times sequential |
| Best fit | Bounded retries, human in the loop pauses, resumable state | Known, fixed sequence of specialized steps | Real delegation ambiguity or review needed between stages |

Bottom line: all three approaches solve this particular task, and sequential and hierarchical produced equally good answers on the captured runs, so hierarchical's extra cost bought no extra quality here. The three still trade off differently as task shape changes: explicit self correction and pausability favor Day 3's graph, a fixed specialist pipeline favors CrewAI sequential, and genuine delegation or review needs favor CrewAI hierarchical, when the task actually has that kind of ambiguity.
