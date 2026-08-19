"""Generates architecture_diagram.png for the notebook and executive report."""

from graphviz import Digraph

g = Digraph("architecture", format="png")
g.attr(rankdir="TB", bgcolor="white", fontname="Helvetica", fontsize="11")
g.attr("node", fontname="Helvetica", fontsize="10", style="filled")

# Entry / IO
g.node("ticket_in", "Incoming ticket\n(email + text)", shape="oval", fillcolor="#E8EAF6")
g.node("api", "FastAPI\nPOST /tickets", shape="box", fillcolor="#C5CAE9")

# Control-flow nodes (LangGraph StateGraph)
g.node("validate", "validate_input\n(failure: bad input)", shape="box", fillcolor="#FFF9C4")
g.node("classify", "classify_ticket\n(LLM)", shape="box", fillcolor="#BBDEFB")
g.node("billing", "billing_lookup\n(tool: SQLite orders db)", shape="box", fillcolor="#B2DFDB")
g.node("fx", "convert_currency\n(tool: FX API,\nfallback on error)", shape="box", fillcolor="#B2DFDB")
g.node("draft", "draft_response\n(LLM)", shape="box", fillcolor="#BBDEFB")
g.node("critique", "critique\n(LLM, self-correction,\nmax 2 retries)", shape="box", fillcolor="#BBDEFB")
g.node("gate", "human_approval_gate\n(interrupt — HUMAN CHECKPOINT)",
       shape="box", fillcolor="#FFCCBC", peripheries="2")
g.node("finalize", "finalize", shape="box", fillcolor="#DCEDC8")
g.node("reject_in", "reject_input", shape="box", fillcolor="#FFCDD2")
g.node("reject_inj", "reject_injection\n(failure: adversarial input)", shape="box", fillcolor="#FFCDD2")
g.node("escalate", "escalate_note\n(failure: tool error)", shape="box", fillcolor="#FFE0B2")

# Data sources / state
g.node("db", "tickets.db\n(SQLite: orders)", shape="cylinder", fillcolor="#D7CCC8")
g.node("fxapi", "Frankfurter FX API\n(external, keyless)", shape="cylinder", fillcolor="#D7CCC8")
g.node("checkpointer", "InMemorySaver\n(checkpointed state per thread_id)", shape="note", fillcolor="#F0F4C3")
g.node("human", "Human approver\n(support lead)", shape="oval", fillcolor="#E8EAF6")
g.node("out", "Response to customer /\nAPI response", shape="oval", fillcolor="#E8EAF6")

g.edge("ticket_in", "api")
g.edge("api", "validate")
g.edge("validate", "reject_in", label="invalid")
g.edge("validate", "classify", label="valid")
g.edge("classify", "reject_inj", label="injection_attempt")
g.edge("classify", "billing", label="refund")
g.edge("classify", "draft", label="technical /\ngeneral_inquiry")
g.edge("billing", "db", style="dashed")
g.edge("billing", "fx")
g.edge("fx", "fxapi", style="dashed")
g.edge("billing", "escalate", label="order not found")
g.edge("billing", "draft", label="order found")
g.edge("draft", "critique")
g.edge("critique", "draft", label="FAIL, retries left")
g.edge("critique", "escalate", label="FAIL, retries exhausted")
g.edge("critique", "gate", label="refund, needs approval")
g.edge("critique", "finalize", label="non-refund / approved")
g.edge("gate", "human", style="dashed", dir="both")
g.edge("gate", "finalize")
g.edge("checkpointer", "gate", style="dotted", label="persists paused state")
g.edge("reject_in", "out")
g.edge("reject_inj", "out")
g.edge("escalate", "out")
g.edge("finalize", "out")

g.render("architecture_diagram", cleanup=True)
print("wrote architecture_diagram.png")
