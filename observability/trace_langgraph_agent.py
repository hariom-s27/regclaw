"""(B1) LangGraph tool-calling agent traced with Langfuse v4.

pip install "langfuse>=4.7" langchain langgraph langchain-anthropic python-dotenv
Tested imports: langgraph 1.2.14, langchain 1.4.3, langchain-anthropic 1.7.5.

What shows up in Langfuse (one trace per task):
  literature-agent (AGENT, our root)          <- task_status lives here
    LangGraph                                 <- graph run (CallbackHandler)
      agent        (node)                     <- each graph node is a nested observation
        ChatAnthropic (GENERATION)            <- usage + model, counted as a model call
      tools        (node)
        query_regulatory_knowledge (TOOL)     <- @tool -> Langfuse type TOOL
"""
import os
import uuid

from langchain_anthropic import ChatAnthropic
from langchain_core.tools import tool
from langfuse.langchain import CallbackHandler
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from lf_common import langfuse, traced_task

MODEL = os.getenv("AGENT_MODEL", "claude-haiku-4-5")


@tool
def query_regulatory_knowledge(query: str) -> str:
    """Run the 4-stage hybrid retriever and return matching clauses."""
    # Stub -- swap in the real retriever from Phase 5.
    return "ISO 10993-7 clause 4.3: EO residual limits ... (stub)"


TOOLS = [query_regulatory_knowledge]
llm = ChatAnthropic(model=MODEL, max_tokens=1024).bind_tools(TOOLS)


def agent_node(state: MessagesState):
    return {"messages": [llm.invoke(state["messages"])]}


builder = StateGraph(MessagesState)
builder.add_node("agent", agent_node)
builder.add_node("tools", ToolNode(TOOLS))
builder.add_edge(START, "agent")
builder.add_conditional_edges("agent", tools_condition)
builder.add_edge("tools", "agent")
graph = builder.compile()


def run(question: str, session_id: str) -> str:
    handler = CallbackHandler()
    with traced_task(
        "literature-agent",
        session_id=session_id,
        agent="literature",
        tags=["langgraph"],
        input={"question": question},
    ) as task:
        out = graph.invoke(
            {"messages": [("user", question)]},
            config={"callbacks": [handler], "recursion_limit": 10},
        )
        task["output"] = out["messages"][-1].content
        return task["output"]


if __name__ == "__main__":
    sid = f"rebuild-{uuid.uuid4().hex[:8]}"
    print(run("What are the ethylene oxide sterilization residual limits?", sid))
    print("session:", sid)
    langfuse.flush()
