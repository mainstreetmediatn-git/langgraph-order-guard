import argparse
import json

from .guard import GuardedOrders


def main():
    parser = argparse.ArgumentParser(description="Execute once or reconcile a reserved local demo order")
    parser.add_argument("--url", required=True)
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--sku", default="A1")
    parser.add_argument("--quantity", type=int, default=1)
    args = parser.parse_args()
    result = GuardedOrders(args.url, args.ledger).execute(args.operation_id, args.sku, args.quantity)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "VERIFIED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
