# Demonstrate it in three minutes

## Before recording

Run installation and both commands in README. Understand why the SQL reservation commits before the POST, how the receiver deduplicates, and why UNKNOWN is an outcome instead of an exception to hide. Use only the synthetic demo records.

## Recording sequence

1. **0:00–0:25 — State the problem.** Open upstream issue #9622. Say: “A server can finish a write even when its response never reaches the agent. Blind retries can repeat the write.” Credit the reporter; do not say you discovered the upstream issue.
2. **0:25–1:00 — Run `python demo.py`.** Point to BEFORE: two orders. AFTER: one order and a verified receipt. FALLBACK: UNKNOWN while lookup is down; VERIFIED when lookup returns, without a second POST.
3. **1:00–1:45 — Explain the mechanism.** Show `GuardedOrders.execute`, `Ledger.reserve`, and `verify`. Then show the receiver transaction. Explain stable business IDs, request fingerprints, atomic receiver deduplication, and receipt matching.
4. **1:45–2:25 — Run `python -m pytest -q`.** Show the crash/restart and sibling-tool tests by name. Explain that a connect-only retry policy can still repeat a completed sibling action when the entire node runs again.
5. **2:25–3:00 — State the boundary.** “This is a tested local application-level mitigation, not a LangGraph core patch or production deployment. UNKNOWN requires reconciliation. A checkpoint does not prove that a remote service never acted.”

## What to publish for credit

Publish a small repository containing this source, tests, pinned dependencies, CI workflow, case study, attribution, and evidence. A public repository gives reviewers a durable artifact and contribution history; upstream acceptance is additional evidence, not a requirement for a portfolio project. A README alone or a renamed fork does not demonstrate a fix.

Suggested repository name: `langgraph-order-guard`.

Suggested description: “Reproduces duplicate side effects under LangGraph ToolNode retries; demonstrates durable operation reservations, receiver idempotency, and receipt verification.”

The public repository is https://github.com/mainstreetmediatn-git/langgraph-order-guard . Review the implementation branch and pull request there, and verify the checks on their exact commit. GitHub commit authorship and contribution-graph attribution depend on the repository workflow and verified commit email; publishing code does not automatically establish upstream contribution credit. No token or password needs to be pasted into chat.

After publishing:

1. Confirm GitHub Actions passes on the published commit. Included CI configuration is not evidence that CI has already passed.
2. Review and run the code yourself. Record your own demo and explain the tradeoffs without reading a script verbatim.
3. Add a README link to the demo and attach the repository to LinkedIn Featured.
4. If seeking an upstream contribution, read current LangGraph CONTRIBUTING guidance, check for competing fixes, and obtain any required issue assignment. Agree on semantics before changing global retry behavior. Do not submit this application wrapper as though it fixes LangGraph core.

## Honest project description after your review and demo

“Developed an AI-assisted LangGraph reliability demonstration reproducing a reported duplicate-order failure. Implemented durable operation reservations, receiver-side deduplication, receipt verification, and an explicit unknown-outcome fallback. Added regression coverage for lost responses, workflow reexecution, concurrent requests, and process crashes.”

Use the exact passing test count from your run if you mention one. Do not claim customer adoption, a production incident you personally handled, bug discovery, an accepted PR, or a fix deployed to LangGraph. The real user report belongs to the upstream reporter; this package supplies your reviewable local engineering exercise. A real-user story requires someone actually using the resulting workflow and providing feedback.
