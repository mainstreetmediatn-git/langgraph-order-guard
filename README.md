# LangGraph Order Guard

Portfolio implementation: https://github.com/mainstreetmediatn-git/langgraph-order-guard

A runnable reproduction and application-level mitigation for [LangGraph issue #9622](https://github.com/langchain-ai/langgraph/issues/9622): a tool commits an order, its HTTP response is lost, and a node retry creates a second order.

This project uses **real LangGraph ToolNode execution, local HTTP sockets, SQLite transactions, and subprocess crash/restart tests**. It needs no LLM, API key, or cloud service. The order receiver and failures are controlled local fixtures, not a production customer integration. It is not a patch to LangGraph core or an accepted upstream contribution.

## Quick start

Python **3.12** is the verified runtime. Extract the ZIP and open a terminal in this folder.

Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe demo.py
.\.venv\Scripts\python.exe -m pytest -q
```

Linux/macOS:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-lock.txt
.venv/bin/python demo.py
.venv/bin/python -m pytest -q
```

No environment activation is required. `requirements-lock.txt` pins the environment used for verification; `requirements.txt` states the direct dependencies. Internet access is needed for installation. Runtime calls use loopback only. Linux/Python 3.12 was run here; Windows/macOS commands are provided but have not been executed here. GitHub Actions runs the same tests and demo; inspect the checks linked to the commit you review. The included evidence directory records the local run captured before publication.

## What the demo proves

| Scenario | Expected observable result |
| --- | --- |
| Unprotected ToolNode, server commits then loses reply | 2 committed orders for 1 request |
| Guarded ToolNode, same lost reply, then workflow replay | 1 order, 1 POST, matching receipt, VERIFIED |
| Receipt endpoint unavailable, then restored | UNKNOWN during outage; VERIFIED after reconciliation; 1 order and 1 POST throughout |

`demo.py` asserts these invariants. It exits unsuccessfully if a claim regresses. The included `evidence/` directory contains this run's captured output and test report; rerun the commands to generate your own evidence.

To demonstrate the same acceptance check failing before and passing after the change:

```bash
python regression_probe.py --unsafe
python regression_probe.py
```

The first command intentionally exits 1 (two orders); the second exits 0 (one verified order). Use the virtual-environment Python path from your platform's quick start. `evidence/red-green.json` records both statuses and output from this run.

## The concrete mitigation

1. The application supplies a **stable business operation ID** before calling a tool. Reuse that ID across retries, process restarts, and graph invocations. Do not generate a new key inside a retried node or derive it only from transient model/tool-call IDs.
2. The client binds the ID to the normalized request and receiver in a durable SQLite ledger, **committing the reservation before POST**. Reusing the ID with different arguments or another receiver is rejected.
3. The receiver atomically stores its operation key and order in the **same SQLite transaction**. A repeated key with the same payload does not create another order; changed payload returns 409.
4. The client queries an authoritative receipt endpoint. `VERIFIED` requires a committed receipt with matching operation ID, order ID, SKU and quantity. A prior receipt's order identity cannot silently change. A successful POST response alone is insufficient.
5. If receipt retrieval fails, returns 404, or disagrees with the request, return **UNKNOWN**, not success or a claim that nothing happened. Retain the reservation. Later invocations reconcile that same ID without another POST.

The fallback is deliberately conservative. A process that dies after reservation but before dispatch leaves a held operation, even if no order exists. This prototype requires operator reconciliation for that case; it does not silently release the key or invent evidence of non-commitment.

## Why a retry-policy change is insufficient

Retrying only connection errors reduces some lost-reply retries, but a ToolNode can contain multiple calls. A first tool can succeed, a second can fail before connection, and a whole-node retry can execute the first tool again. `test_sibling_tool_failure_retries_completed_tool` demonstrates both unprotected duplication and protected behavior using a real ToolNode and a connect-only retry policy.

## Test scope

The suite covers:

- The original duplicate-order reproducer and receipt-based recovery.
- Whole-node reexecution and multiple tools with a sibling failure.
- Receipt outage, HTTP 200 without verification, malformed/mismatched receipts and changed receipt identity.
- Payload/key conflicts and binding operations to a receiver.
- Concurrent clients sharing a ledger and concurrent repeated HTTP requests.
- An actual subprocess exiting after the remote commit, followed by reconciliation in a fresh subprocess.
- A subprocess exiting after reservation but before dispatch, retaining UNKNOWN without sending a new write.
- Receiver restart retaining its deduplication data.
- HTTP rejection without blind retry and invalid input rejection.

The guard's ledger persists business-operation state; this demo does **not** implement LangGraph checkpoint/resume, distributed leases, or a general exactly-once agent runtime. The restart test starts a new client process against a still-running local receiver. Receiver restart is tested separately.

## Files

- `order_guard/workflow.py`: LangGraph integration and intentionally unsafe baseline.
- `order_guard/guard.py`: reservation, request binding, receipt verification and UNKNOWN fallback.
- `order_guard/service.py`: synthetic HTTP receiver, atomic deduplication, injected lost replies.
- `tests/test_guard.py`: failure and regression tests.
- `demo.py`: reproducible before/after/fallback demonstration.
- `DEMONSTRATION.md`: recording script and GitHub credit checklist.
- `CASE_STUDY.md`: honest portfolio narrative and boundaries.
- `.github/workflows/tests.yml`: GitHub Actions workflow for installation, dependency checks, tests, and demo validation.

## Limits and production integration

This protects a particular order-writing contract; it cannot make arbitrary third-party tools exactly-once. A real receiver must support durable idempotency and trustworthy lookup by operation ID. If you cannot control it, use its supported idempotency API and reconciliation contract. A local ledger alone cannot prove an external side effect did or did not occur.

For a real deployment, add authenticated HTTPS, tenant-scoped operation IDs, receiver authorization, receipt consistency/retention rules, durable shared storage, operational monitoring, and a defined reconciliation owner. Keep keys at least as long as replays are possible; deleting or replacing them weakens the guarantee. A matching receipt assumes a trusted receiver: this demo does not cryptographically prove that a dishonest service fulfilled an order. Fulfillment and payment settlement are outside scope.

Do not turn UNKNOWN into success in a UI or downstream agent. Do not bypass a held reservation by inventing a new operation ID. Distinct legitimate orders must have distinct IDs.

## Attribution

Original bug report and minimal failure pattern: Sankalp Gilda / astrogilda, [issue #9622](https://github.com/langchain-ai/langgraph/issues/9622), opened November 7, 2026. This demo independently implements a local receiver, reproducer, guard and tests around the reported behavior. LangGraph is a separate upstream project; this is not endorsed by its maintainers.

Prepared with AI-assisted implementation and verification for Nick Gardiner / Main Street Media Co. Nick should run, review and understand the implementation before presenting it as his engineering work. No production user, customer outcome, upstream acceptance, or personal debugging experience is asserted by this package.
