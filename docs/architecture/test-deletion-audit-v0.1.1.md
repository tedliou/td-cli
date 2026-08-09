# v0.1.1 test deletion audit

The deletion test was applied before changing the suite: remove a test only when another test
through the same or a deeper module interface detects the same material failure.

## Removed

- The oversized-result half of
  `test_agent_rejects_invalid_expression_and_oversized_result_with_typed_errors` duplicated
  `test_accept_records_internal_and_oversized_outcomes`. The retained test crosses
  `AgentExt.accept`, asserts the same `result_too_large` failure, and also verifies the event.
- Repeated hand-written Command inventories were replaced by a contract between the host
  `CommandCatalog` and the Agent registration interface.

## Retained

- `test_workflow_contract.py`: despite using textual assertions, it is the only automated
  interface protecting Release publication order, draft lookup, hosted staging, and pinned
  Actions. It can be replaced only after workflow logic has an executable module interface.
- Agent builder and callback tests: each executes a distinct TouchDesigner build or callback
  interface and protects extension activation ordering, expression validity, and generated DAT
  topology.
- Agent source-revision tests: they uniquely protect stable identity across CRLF checkouts.

This audit does not treat age or incident origin as grounds for deletion. A retained regression
test remains useful until an equal or deeper interface test replaces it.
