"""Local HTTP fixture: real sockets, SQLite commits and deliberate lost replies."""

import json
import sqlite3
import threading
import uuid
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .guard import operation_key, request_payload


class OrderService:
    def __init__(self, database: str | Path, *, drop_reply=False):
        self.database = str(database)
        self.drop_reply = drop_reply
        self.verify_available = True
        self.receipt_override = None
        self.reject_post = False
        self._lock = threading.Lock()
        self.posts = 0
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS orders (
                operation_id TEXT PRIMARY KEY, order_id TEXT NOT NULL,
                sku TEXT NOT NULL, quantity INTEGER NOT NULL)""")
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def respond(self, status, data):
                body = json.dumps(data).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                if self.path != "/orders":
                    return self.respond(404, {})
                with owner._lock:
                    owner.posts += 1
                    lose_reply = owner.drop_reply
                    owner.drop_reply = False
                if owner.reject_post:
                    return self.respond(503, {"error": "unavailable"})
                try:
                    data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                    payload = request_payload(data["sku"], data["quantity"])
                    # Missing a key models the original unsafe service.
                    key = operation_key(self.headers.get("Idempotency-Key") or uuid.uuid4().hex)
                except (ValueError, TypeError, KeyError):
                    return self.respond(400, {"error": "invalid order"})
                with closing(sqlite3.connect(owner.database, timeout=10)) as db, db:
                    db.execute("PRAGMA synchronous=FULL")
                    db.execute("BEGIN IMMEDIATE")
                    old = db.execute("SELECT order_id, sku, quantity FROM orders WHERE operation_id=?", (key,)).fetchone()
                    if old and (old[1] != payload["sku"] or old[2] != payload["quantity"]):
                        return self.respond(409, {"error": "operation key conflict"})
                    if not old:
                        db.execute("INSERT INTO orders VALUES (?, ?, ?, ?)",
                                   (key, uuid.uuid4().hex, payload["sku"], payload["quantity"]))
                # COMMIT has completed before dropping the connection.
                if lose_reply:
                    self.close_connection = True
                    return
                self.respond(200, {"accepted": True})

            def do_GET(self):
                if not self.path.startswith("/receipts/"):
                    return self.respond(404, {})
                if not owner.verify_available:
                    return self.respond(503, {"error": "receipt service unavailable"})
                key = self.path.removeprefix("/receipts/")
                with closing(sqlite3.connect(owner.database, timeout=10)) as db:
                    row = db.execute("SELECT order_id, sku, quantity FROM orders WHERE operation_id=?", (key,)).fetchone()
                if not row:
                    return self.respond(404, {"error": "no visible receipt"})
                receipt = {"operation_id": key, "order_id": row[0], "sku": row[1],
                           "quantity": row[2], "status": "committed"}
                if owner.receipt_override is not None:
                    receipt = owner.receipt_override
                self.respond(200, receipt)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def count(self):
        with closing(sqlite3.connect(self.database, timeout=10)) as db:
            return db.execute("SELECT count(*) FROM orders").fetchone()[0]
