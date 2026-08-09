# TouchDesigner diagnostic bridge

This is a session-only development tool for diagnosing the locked TouchDesigner runtime. It is
not part of the Agent Component or any Release Artifact.

## Safety

The bridge executes arbitrary Python with the permissions of TouchDesigner. It is not a sandbox:
authorized code can modify the project, read or write files, start processes, or hang the UI.
Use it only in an unsaved, disposable test project. Never commit or share the generated token.

The server uses Python's `ThreadingHTTPServer` and always binds to `127.0.0.1`. TouchDesigner
2025.32050 predates the Web Server DAT `Local Address` parameter, so that DAT cannot meet the
loopback-only requirement in the locked runtime.

## Bootstrap

Run the single line from `td-diagnostic-bridge-command.txt` in TouchDesigner Textport. A successful
start prints only:

```text
TD_DIAGNOSTIC_BRIDGE_READY http://127.0.0.1:9983
```

The bootstrap writes a one-time token and endpoint to
`%LOCALAPPDATA%\touchdesigner-cli\diagnostic-bridge.json`.

## Agent commands

```powershell
uv run --python 3.11 python tools/td_diagnostic_bridge/client.py health
uv run --python 3.11 python tools/td_diagnostic_bridge/client.py exec --code "result = app.build"
uv run --python 3.11 python tools/td_diagnostic_bridge/client.py exec --file probe.py
uv run --python 3.11 python tools/td_diagnostic_bridge/client.py shutdown
```

`shutdown` authenticates first, then the Execute DAT stops the HTTP server on the TouchDesigner main
thread, deletes the token file, and destroys `/project1/td_diagnostic_bridge` at frame end. Confirm
that port 9983 is no longer listening. Do not save the test project; closing without saving is the
final cleanup for any other diagnostic operators.
