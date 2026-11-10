"""Reserve before sending; reconcile by receipt before claiming success.

No automatic second POST for a reserved operation. This intentionally trades
availability for avoiding duplicate writes when the remote outcome is unknown.
"""

import hashlib
import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit

import httpx


class OperationConflict(ValueError):
    """An operation key was reused for a different request or receiver."""


def request_payload(sku: str, quantity: int) -> dict:
    if not isinstance(sku, str) or not sku.strip() or len(sku.strip()) > 80:
        raise ValueError("sku must be a nonempty string of at most 80 characters")
    if type(quantity) is not int or not 1 <= quantity <= 10000:
        raise ValueError("quantity must be an integer between 1 and 10000")
    return {"sku": sku.strip(), "quantity": quantity}


def operation_key(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value):
        raise ValueError("operation_id must be 1–100 letters, digits, underscores or hyphens")
    return value


class Ledger:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path == ":memory:":
            raise ValueError("Use a persistent ledger file")
        with closing(self.connect()) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS operations (
                operation_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
                endpoint TEXT NOT NULL, state TEXT NOT NULL,
                receipt TEXT, detail TEXT NOT NULL DEFAULT '')""")

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        return db

    def reserve(self, key: str, payload: dict, endpoint: str) -> tuple[bool, dict]:
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM operations WHERE operation_id=?", (key,)).fetchone()
            if existing:
                if existing["fingerprint"] != fingerprint or existing["endpoint"] != endpoint:
                    raise OperationConflict("operation_id already belongs to a different request or receiver")
                return False, dict(existing)
            db.execute("INSERT INTO operations VALUES (?, ?, ?, 'UNKNOWN', NULL, ?)",
                       (key, fingerprint, endpoint, "Reserved before dispatch; outcome not verified"))
            return True, {"receipt": None}

    def record(self, key: str, state: str, receipt: dict | None, detail: str):
        with closing(self.connect()) as db, db:
            if receipt is None:
                db.execute("UPDATE operations SET state=?, detail=? WHERE operation_id=?", (state, detail, key))
            else:
                # A concurrent verifier must not silently replace a different receipt.
                db.execute("BEGIN IMMEDIATE")
                old = db.execute("SELECT receipt FROM operations WHERE operation_id=?", (key,)).fetchone()
                if old[0] and json.loads(old[0])["order_id"] != receipt["order_id"]:
                    raise OperationConflict("receiver changed the order identity for this operation")
                db.execute("UPDATE operations SET state=?, receipt=?, detail=? WHERE operation_id=?",
                           (state, json.dumps(receipt, sort_keys=True), detail, key))


class GuardedOrders:
    def __init__(self, base_url: str, ledger_path: str | Path):
        url = urlsplit(base_url)
        # This distributable is a loopback-only demonstration, not a production connector.
        if (url.scheme != "http" or url.hostname not in ("127.0.0.1", "localhost", "::1")
                or url.username or url.password or url.query or url.fragment or url.path not in ("", "/")):
            raise ValueError("Demo receiver must be a loopback HTTP origin")
        self.base_url = base_url.rstrip("/")
        self.ledger = Ledger(ledger_path)

    def execute(self, operation_id: str, sku: str, quantity: int) -> dict:
        key = operation_key(operation_id)
        payload = request_payload(sku, quantity)
        first, _ = self.ledger.reserve(key, payload, self.base_url)
        if first:
            try:
                with httpx.Client(timeout=2, trust_env=False) as client:
                    client.post(self.base_url + "/orders", json=payload,
                                headers={"Idempotency-Key": key}).raise_for_status()
            except httpx.HTTPError:
                # A lost response or HTTP error cannot prove whether a write committed.
                # Do not resend. Even a 200 response still requires receipt verification.
                pass
        return self.verify(key, payload)

    def verify(self, key: str, payload: dict) -> dict:
        try:
            with httpx.Client(timeout=2, trust_env=False) as client:
                response = client.get(self.base_url + "/receipts/" + key)
                response.raise_for_status()
                receipt = response.json()
            if (not isinstance(receipt, dict) or receipt.get("operation_id") != key
                    or receipt.get("status") != "committed"
                    or not isinstance(receipt.get("order_id"), str) or not receipt["order_id"]
                    or receipt.get("sku") != payload["sku"]
                    or type(receipt.get("quantity")) is not int
                    or receipt["quantity"] != payload["quantity"]):
                raise ValueError("Receipt does not match the requested business action")
            self.ledger.record(key, "VERIFIED", receipt, "Receiver receipt matched operation and payload")
            return {"status": "VERIFIED", "operation_id": key, "receipt": receipt}
        except (httpx.HTTPError, ValueError) as exc:
            detail = "Receipt unavailable or inconsistent: " + type(exc).__name__
            self.ledger.record(key, "UNKNOWN", None, detail)
            return {"status": "UNKNOWN", "operation_id": key,
                    "next_action": "Reconcile this same operation; do not create a new key or resend blindly",
                    "detail": detail}
