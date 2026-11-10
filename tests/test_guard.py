import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import RetryPolicy

from order_guard.guard import GuardedOrders, OperationConflict
from order_guard.service import OrderService
from order_guard.workflow import build_graph, invoke_order


@pytest.fixture
def receiver(tmp_path):
    with OrderService(tmp_path / "remote.db", drop_reply=True) as server:
        yield server


def result(graph, **kwargs):
    return json.loads(invoke_order(graph, **kwargs)["messages"][-1].content)


def test_original_failure_reproduces_two_orders(receiver):
    outcome = result(build_graph(receiver.url, safe=False))
    assert outcome["status"] == "CLAIMED_SUCCESS_WITHOUT_VERIFICATION"
    assert receiver.count() == 2


def test_lost_reply_is_reconciled_without_duplicate(receiver, tmp_path):
    graph = build_graph(receiver.url, tmp_path / "ledger.db")
    outcome = result(graph)
    assert outcome["status"] == "VERIFIED"
    assert outcome["receipt"]["status"] == "committed"
    assert receiver.count() == receiver.posts == 1


def test_whole_node_reexecution_does_not_repeat_completed_order(receiver, tmp_path):
    graph = build_graph(receiver.url, tmp_path / "ledger.db")
    first = result(graph)
    second = result(graph)
    assert first["receipt"] == second["receipt"]
    assert receiver.count() == receiver.posts == 1


@pytest.mark.parametrize("safe, expected_orders", [(False, 2), (True, 1)])
def test_sibling_tool_failure_retries_completed_tool(receiver, tmp_path, safe, expected_orders):
    """A connect-only retry can still repeat a different tool that already succeeded."""
    receiver.drop_reply = False
    guard = GuardedOrders(receiver.url, tmp_path / "ledger.db")
    attempts = []

    @tool
    def place_order() -> dict:
        """Place the requested order."""
        if safe:
            return guard.execute("sibling-case", "A1", 1)
        with httpx.Client(trust_env=False) as client:
            client.post(receiver.url + "/orders", json={"sku": "A1", "quantity": 1}).raise_for_status()
        return {"status": "unverified"}

    @tool
    def other_tool() -> str:
        """A separate tool fails before connecting on its first attempt."""
        attempts.append(1)
        if len(attempts) == 1:
            raise httpx.ConnectError("Injected sibling connect failure")
        return "ok"

    builder = StateGraph(MessagesState)
    builder.add_node("tools", ToolNode([place_order, other_tool], handle_tool_errors=False),
                     retry_policy=RetryPolicy(initial_interval=0.01, max_attempts=2,
                                              jitter=False, retry_on=httpx.ConnectError))
    builder.add_edge(START, "tools")
    builder.add_edge("tools", END)
    app = builder.compile()
    app.invoke({"messages": [AIMessage(content="", tool_calls=[
        {"name": "place_order", "id": "first", "args": {}},
        {"name": "other_tool", "id": "second", "args": {}},
    ])]}, config={"max_concurrency": 1})
    assert len(attempts) == 2
    assert receiver.count() == expected_orders


def test_unknown_is_preserved_until_receipt_available(receiver, tmp_path):
    receiver.verify_available = False
    graph = build_graph(receiver.url, tmp_path / "ledger.db")
    assert result(graph)["status"] == "UNKNOWN"
    assert result(graph)["status"] == "UNKNOWN"
    assert receiver.count() == receiver.posts == 1
    receiver.verify_available = True
    assert result(graph)["status"] == "VERIFIED"
    assert receiver.count() == receiver.posts == 1


def test_http_200_without_verification_is_not_success(receiver, tmp_path):
    receiver.drop_reply = False
    receiver.verify_available = False
    assert result(build_graph(receiver.url, tmp_path / "ledger.db"))["status"] == "UNKNOWN"
    assert receiver.count() == 1


@pytest.mark.parametrize("override", [
    {}, {"status": "committed"}, [],
    {"operation_id": "demo-order-001", "order_id": "x", "sku": "OTHER", "quantity": 1, "status": "committed"},
    {"operation_id": "demo-order-001", "order_id": "x", "sku": "A1", "quantity": True, "status": "committed"},
    {"operation_id": "WRONG", "order_id": "x", "sku": "A1", "quantity": 1, "status": "committed"},
])
def test_malformed_or_mismatched_receipt_never_claims_success(receiver, tmp_path, override):
    receiver.receipt_override = override
    assert result(build_graph(receiver.url, tmp_path / "ledger.db"))["status"] == "UNKNOWN"


def test_reusing_key_with_changed_payload_fails_before_post(receiver, tmp_path):
    client = GuardedOrders(receiver.url, tmp_path / "ledger.db")
    assert client.execute("same", "A1", 1)["status"] == "VERIFIED"
    with pytest.raises(OperationConflict):
        client.execute("same", "A1", 2)
    assert receiver.posts == 1


def test_local_operation_is_bound_to_receiver(receiver, tmp_path):
    ledger = tmp_path / "ledger.db"
    GuardedOrders(receiver.url, ledger).execute("same", "A1", 1)
    with OrderService(tmp_path / "other.db") as other:
        with pytest.raises(OperationConflict):
            GuardedOrders(other.url, ledger).execute("same", "A1", 1)
        assert other.posts == 0


def test_receiver_deduplicates_repeated_http_posts(receiver):
    receiver.drop_reply = False
    def send(_):
        with httpx.Client(trust_env=False, timeout=5) as client:
            return client.post(receiver.url + "/orders", json={"sku": "A1", "quantity": 1},
                               headers={"Idempotency-Key": "same"}).status_code
    with ThreadPoolExecutor(max_workers=4) as workers:
        assert list(workers.map(send, range(8))) == [200] * 8
    assert receiver.count() == 1


def test_receiver_rejects_key_payload_conflict(receiver):
    receiver.drop_reply = False
    with httpx.Client(trust_env=False) as client:
        assert client.post(receiver.url + "/orders", json={"sku": "A1", "quantity": 1},
                           headers={"Idempotency-Key": "same"}).status_code == 200
        assert client.post(receiver.url + "/orders", json={"sku": "A1", "quantity": 2},
                           headers={"Idempotency-Key": "same"}).status_code == 409
    assert receiver.count() == 1


def test_receiver_restart_retains_idempotency_record(tmp_path):
    database = tmp_path / "remote.db"
    def post(server):
        with httpx.Client(trust_env=False) as client:
            return client.post(server.url + "/orders", json={"sku": "A1", "quantity": 1},
                               headers={"Idempotency-Key": "persistent"}).status_code
    with OrderService(database) as first:
        assert post(first) == 200
        assert first.count() == 1
    with OrderService(database) as restarted:
        assert post(restarted) == 200
        assert restarted.count() == 1


def test_concurrent_clients_share_one_reservation(receiver, tmp_path):
    path = tmp_path / "ledger.db"
    GuardedOrders(receiver.url, path)  # initialize before competing processes/threads
    def send(_):
        return GuardedOrders(receiver.url, path).execute("same", "A1", 1)
    with ThreadPoolExecutor(max_workers=4) as workers:
        outcomes = list(workers.map(send, range(8)))
    assert all(x["status"] in ("VERIFIED", "UNKNOWN") for x in outcomes)
    assert receiver.count() == receiver.posts == 1
    assert send(0)["status"] == "VERIFIED"


def test_crash_after_commit_reconciles_in_fresh_process(receiver, tmp_path):
    ledger = tmp_path / "ledger.db"
    script = """
import os, sys
from order_guard.guard import GuardedOrders
guard = GuardedOrders(sys.argv[1], sys.argv[2])
guard.verify = lambda *args: os._exit(23)
guard.execute('crash-case', 'A1', 1)
"""
    crashed = subprocess.run([sys.executable, "-c", script, receiver.url, str(ledger)], capture_output=True)
    assert crashed.returncode == 23
    assert receiver.count() == 1
    resumed = subprocess.run([sys.executable, "-m", "order_guard", "--url", receiver.url,
                              "--ledger", str(ledger), "--operation-id", "crash-case"],
                             capture_output=True, text=True)
    assert resumed.returncode == 0, resumed.stderr
    assert json.loads(resumed.stdout)["status"] == "VERIFIED"
    assert receiver.posts == receiver.count() == 1


def test_reservation_before_dispatch_crash_stays_unknown(receiver, tmp_path):
    ledger = tmp_path / "ledger.db"
    script = """
import os, sys
from order_guard.guard import GuardedOrders
guard = GuardedOrders(sys.argv[1], sys.argv[2])
guard.ledger.reserve('never-dispatched', {'sku': 'A1', 'quantity': 1}, guard.base_url)
os._exit(24)
"""
    crashed = subprocess.run([sys.executable, "-c", script, receiver.url, str(ledger)], capture_output=True)
    assert crashed.returncode == 24
    resumed = subprocess.run([sys.executable, "-m", "order_guard", "--url", receiver.url,
                              "--ledger", str(ledger), "--operation-id", "never-dispatched"],
                             capture_output=True, text=True)
    assert resumed.returncode == 2
    assert json.loads(resumed.stdout)["status"] == "UNKNOWN"
    assert receiver.count() == receiver.posts == 0


def test_post_rejection_does_not_trigger_blind_retry(receiver, tmp_path):
    receiver.reject_post = True
    graph = build_graph(receiver.url, tmp_path / "ledger.db")
    assert result(graph)["status"] == "UNKNOWN"
    assert result(graph)["status"] == "UNKNOWN"
    assert receiver.posts == 1
    assert receiver.count() == 0


def test_changed_receipt_identity_is_not_accepted(receiver, tmp_path):
    client = GuardedOrders(receiver.url, tmp_path / "ledger.db")
    verified = client.execute("same", "A1", 1)
    receipt = dict(verified["receipt"], order_id="different-order")
    receiver.receipt_override = receipt
    assert client.execute("same", "A1", 1)["status"] == "UNKNOWN"


@pytest.mark.parametrize("quantity", [0, -1, True, 1.5, 10001])
def test_invalid_quantity_fails_before_dispatch(receiver, tmp_path, quantity):
    with pytest.raises(ValueError):
        GuardedOrders(receiver.url, tmp_path / "ledger.db").execute("same", "A1", quantity)
    assert receiver.posts == 0


def test_nonlocal_receiver_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        GuardedOrders("https://example.com", tmp_path / "ledger.db")
