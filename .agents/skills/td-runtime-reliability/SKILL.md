---
name: td-runtime-reliability
description: Review and implement td-cli runtime reliability changes involving Protocol messages, Daemon transport, RequestLifecycle, RequestStore, Agent callbacks or scheduling, Socket.IO integration, or locked TouchDesigner acceptance. Do not use for ordinary CLI presentation, release-note, or documentation-only edits.
---

# TouchDesigner runtime reliability

Use this workflow before changing a runtime seam and again before declaring it complete.

1. Read `CONTEXT.md` and the relevant implementation. For reliability claims, read
   `docs/research/runtime-reliability-primary-sources.md` and verify any drift-prone library or
   TouchDesigner fact against its primary documentation.
2. State which Request and TouchDesigner Instance invariants the change affects. Preserve
   persistence-before-dispatch, per-Instance FIFO and one authorized Request, Connection ID
   generation isolation, retained outcomes, honest `unknown` semantics, and main-thread
   TouchDesigner object access unless an approved specification explicitly changes one.
3. Enumerate only distinct failure edges introduced or changed: cancellation, concurrent identity,
   process death, disconnect generation, authorization/outcome windows, capacity, timer expiry,
   migration, and controlled shutdown. Define the observable state for each edge before coding.
4. Select the smallest public test seam from the Command catalog, RequestStore,
   RequestLifecycle, HTTP/Socket.IO adapter, or Agent request scheduler. Run one red-to-green
   vertical slice at a time. Replace superseded shallow tests; do not repeat the same failure matrix
   for every Command.
5. Apply the primary-source design rules recorded in the research baseline. Following Parnas,
   place a seam around a volatile design decision, not around a processing step. Use McCabe
   complexity to locate decision-heavy code, not as a numeric target; lower it by moving policy
   behind a deeper Interface, never by adding forwarding wrappers. Following SWEBOK's risk-based
   testing principle, spend integration and locked-runtime cost on distinct high-impact failure
   modes and delete subset reruns or tests already proved at a smaller seam.
6. Use locked TouchDesigner 2025.32050 only for facts substitutes cannot prove: callback and
   scheduler thread, paused-timeline progress, graph mutation, frame stall, reconnect, process
   death, Agent reload, and retained-outcome replay. Record the build, artifact identity, exact
   probes, measurements, margins, and observed outcomes. A failed required probe blocks release.

Finish only when focused and full automated gates pass, runtime documentation matches observable
behavior, and every changed invariant has either deterministic integration evidence or a recorded
locked-runtime acceptance result.
