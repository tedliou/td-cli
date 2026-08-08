# Phase 1 review evidence

This is the automated handoff for **Implement the Phase 1 daemon and Agent
Component vertical slice**. Real TouchDesigner loading remains the child ticket
**Validate the Phase 1 Agent Component in locked TouchDesigner**.

## Automated seams

- Strict Protocol v1 diagnostic Command and durable Request snapshots.
- Authenticated management health, Instance listing, Request submission/query,
  deduplication, conflict handling, and restart recovery.
- Real loopback Socket.IO authentication, registration, connection generations,
  application heartbeat, offline retention, single-dispatch FIFO, capacity 32,
  result durability/acknowledgement, reconnect, and draining.
- Persistent 256-bit token, SQLite WAL/FULL durability and schema checks,
  seven-day terminal retention, bounded redacted JSON logging, and
  source/artifact inspection.

Artifact evidence records the actual TouchDesigner-built child topology and the
SHA-256 of the resulting `.tox`; inspection recomputes the digest and rejects a
replaced, damaged, stale, wrong-version, or wrong-topology artifact.

```powershell
uv run --python 3.11 pytest -q
uv run --python 3.11 ruff check .
uv run --python 3.11 ruff format --check .
```

## Windows lifecycle smoke test

Point `LOCALAPPDATA` at a fresh temporary directory, then run `td-daemon start`,
`status --json`, `stop`, and a final `status --json`. Expected exit codes are
`0`, `0`, `0`, and `3`; the final status is `stopped`, `run\daemon.json` is
absent, and token/database/log files remain.
