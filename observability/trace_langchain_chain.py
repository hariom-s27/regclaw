"""(B2) LangChain chain traced with the Langfuse v4 CallbackHandler.

pip install "langfuse>=4.7" langchain langchain-anthropic python-dotenv
NOTE: the Langfuse handler imports the full `langchain` package, not only
langchain-core -- without it you get ModuleNotFoundError at import time.
"""
import os
import uuid

from langchain_anthropic import ChatAnthropic
from langchain_core.prompts import ChatPromptTemplate
from langfuse.langchain import CallbackHandler  # v3+ path (v2 was langfuse.callback)

from lf_common import langfuse, traced_task

MODEL = os.getenv("CLAIM_CHECK_MODEL", "claude-haiku-4-5")

prompt = ChatPromptTemplate.from_template(
    "Reply SUPPORTED or UNSUPPORTED.\nClaim: {claim}\nSource text: {source}"
)
chain = prompt | ChatAnthropic(model=MODEL, max_tokens=50)


def check_claim(claim: str, source: str, session_id: str) -> str:
    # The handler takes no user/session args in v3/v4 (CallbackHandler(public_key=, trace_context=)).
    # Trace attributes come from propagate_attributes() inside traced_task.
    handler = CallbackHandler()
    with traced_task(
        "pageindex-citation-check",
        session_id=session_id,
        agent="pageindex",
        tags=["citation-check"],
        input={"claim": claim},
    ) as task:
        result = chain.invoke(
            {"claim": claim, "source": source},
            config={"callbacks": [handler], "run_name": "citation-check-chain"},
        )
        task["output"] = result.content
        return result.content


if __name__ == "__main__":
    sid = f"rebuild-{uuid.uuid4().hex[:8]}"
    print(check_claim("Storage must be below 25 C.", "Store below 25 C.", sid))
    langfuse.flush()  # short-lived script: always flush (or shutdown) before exit
