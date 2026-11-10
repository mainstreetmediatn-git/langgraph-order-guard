"""The same acceptance contract: fails against baseline, passes against mitigation."""

import argparse
import json
import tempfile
from pathlib import Path

from order_guard.service import OrderService
from order_guard.workflow import build_graph, invoke_order


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--unsafe", action="store_true", help="Run the intentionally broken baseline")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        with OrderService(root / "orders.db", drop_reply=True) as receiver:
            graph = build_graph(receiver.url, root / "ledger.db", safe=not args.unsafe)
            outcome = json.loads(invoke_order(graph)["messages"][-1].content)
            count = receiver.count()
            print(json.dumps({"unsafe": args.unsafe, "orders_committed": count, "outcome": outcome["status"]}))
            assert count == 1, f"REGRESSION: one business request committed {count} orders"
            assert outcome["status"] == "VERIFIED", "Success requires a matching committed receipt"
    print("PASS: exactly one local order, with receipt verification")


if __name__ == "__main__":
    main()
