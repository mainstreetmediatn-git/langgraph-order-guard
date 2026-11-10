# Case study: an order succeeds, its reply vanishes, and an agent retries

**Stage:** independently runnable local engineering demonstration, prepared for Nick Gardiner with AI assistance. Not deployed; no live customer or upstream acceptance is claimed.

**Source:** [LangGraph #9622](https://github.com/langchain-ai/langgraph/issues/9622), reported by Sankalp Gilda (astrogilda). The issue was open when reviewed November 10, 2026. This is an application-level mitigation and not a claim to have discovered or repaired the upstream library.

## Reproduced failure

The fixture accepts a POST and commits an order to SQLite, then intentionally closes the connection before responding. A real LangGraph ToolNode with a RetryPolicy runs the tool again. Without an operation key, the receiver creates another row. The graph returns its success-shaped result while the receiver contains two orders. The baseline uses a controlled local service instead of a live commerce API.

## Root cause

A transport error describes what the caller observed, not whether the receiver committed. Retrying the whole node repeats tool execution. Graph state and remote business state are separate commit domains, so persisted graph state alone cannot establish whether an external side effect happened.

## Implementation

Reserve a stable business operation ID in a SQLite ledger before dispatch. Bind it to the normalized payload and receiver. Deduplicate at the receiver with the operation key and order in one transaction. Before reporting VERIFIED, fetch a receipt and validate its committed status, operation key, order identity, SKU and quantity.

If verification is missing, unavailable or inconsistent, retain UNKNOWN and reconcile later. Do not silently retry POST. This preserves safety after a reply loss and after a process exit between remote commit and local verification. It deliberately holds a reservation after a pre-dispatch crash instead of guessing that a new attempt is safe.

## Additional failure investigated

A second tool can fail before connecting after a first tool succeeds. A connect-only node retry can still repeat the completed first tool. The regression test executes both calls sequentially inside one real ToolNode, injects the sibling failure, and compares the unprotected and guarded counts. This is why a narrower retry exception list alone is not a complete remedy.

## Evidence

Run `python demo.py` and `python -m pytest -q`. See captured demo output, environment details and JUnit results in `evidence/`. The test suite exercises network response loss, malformed receipts, conflicting keys, concurrent execution, child-process exits and persisted receiver records. CI is configured but not yet observed on GitHub. The real upstream report is external evidence; local tests are separate evidence of this mitigation.

## Limits

The application wrapper requires a durable idempotency/lookup contract from the receiver. It is not general exactly-once execution, distributed orchestration, graph checkpoint recovery, real-user adoption, or a complete production integration. UNKNOWN requires a reconciliation owner. Matching receipts assume an honest, trusted service. Model reasoning is deliberately absent so transport and side-effect semantics can be tested deterministically.

## Interview discussion

Explain why a business operation key must survive retries, why payload changes must be rejected for the same key, why a 404 does not justify a blind resend, and why UNKNOWN is safer than guessing success. Discuss the availability cost of reserving before dispatch and which receiver contract would permit a safe retry. Describe the original reporter's work and your AI-assisted implementation separately.
