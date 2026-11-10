"""Actual LangGraph ToolNode with node-level retries, no model/API key required."""

import httpx
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import RetryPolicy

from .guard import GuardedOrders


def build_graph(base_url, ledger_path=None, *, safe=True):
    guard = GuardedOrders(base_url, ledger_path) if safe else None

    @tool
    def create_order(operation_id: str, sku: str, quantity: int) -> dict:
        """Request an order and report whether its committed receipt was verified."""
        if guard is not None:
            return guard.execute(operation_id, sku, quantity)
        with httpx.Client(timeout=2, trust_env=False) as client:
            client.post(base_url + "/orders", json={"sku": sku, "quantity": quantity}).raise_for_status()
        return {"status": "CLAIMED_SUCCESS_WITHOUT_VERIFICATION"}

    graph = StateGraph(MessagesState)
    graph.add_node("tools", ToolNode([create_order], handle_tool_errors=False),
                   retry_policy=RetryPolicy(initial_interval=0.01, backoff_factor=1,
                                            max_interval=0.01, max_attempts=2, jitter=False))
    graph.add_edge(START, "tools")
    graph.add_edge("tools", END)
    return graph.compile()


def order_call(operation_id="demo-order-001", sku="A1", quantity=1):
    return {"name": "create_order", "id": "tool-" + operation_id,
            "args": {"operation_id": operation_id, "sku": sku, "quantity": quantity}}


def invoke_order(graph, **kwargs):
    return graph.invoke({"messages": [AIMessage(content="", tool_calls=[order_call(**kwargs)])]})
