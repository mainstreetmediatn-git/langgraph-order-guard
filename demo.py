"""Three assertions make the portfolio demo fail loudly if its promises regress."""

import json
import tempfile
from importlib.metadata import version
from pathlib import Path

from order_guard.service import OrderService
from order_guard.workflow import build_graph, invoke_order


def run():
    report = {"langgraph": version("langgraph"), "scenarios": []}
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        with OrderService(root / "unsafe.db", drop_reply=True) as service:
            invoke_order(build_graph(service.url, safe=False))
            assert service.count() == 2
            report["scenarios"].append({"scenario": "BEFORE: lost reply + node retry",
                                        "orders_committed": service.count(), "duplicate": True})
        with OrderService(root / "safe.db", drop_reply=True) as service:
            graph = build_graph(service.url, root / "safe-ledger.db")
            first = json.loads(invoke_order(graph)["messages"][-1].content)
            replay = json.loads(invoke_order(graph)["messages"][-1].content)
            assert first["status"] == replay["status"] == "VERIFIED"
            assert first["receipt"] == replay["receipt"]
            assert service.posts == service.count() == 1
            report["scenarios"].append({"scenario": "AFTER: lost reply + verified receipt + replay",
                                        "orders_committed": 1, "post_attempts": service.posts,
                                        "status": first["status"], "same_receipt_on_replay": True})
        with OrderService(root / "unknown.db", drop_reply=True) as service:
            service.verify_available = False
            graph = build_graph(service.url, root / "unknown-ledger.db")
            unknown = json.loads(invoke_order(graph)["messages"][-1].content)
            assert unknown["status"] == "UNKNOWN"
            service.verify_available = True
            recovered = json.loads(invoke_order(graph)["messages"][-1].content)
            assert recovered["status"] == "VERIFIED"
            assert service.count() == service.posts == 1
            report["scenarios"].append({"scenario": "FALLBACK: verification outage then reconciliation",
                                        "during_outage": unknown["status"], "after_recovery": recovered["status"],
                                        "orders_committed": 1, "post_attempts": 1})
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    run()
