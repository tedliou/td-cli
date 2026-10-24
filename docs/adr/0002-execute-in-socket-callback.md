---
Status: accepted
---

# Agent 在 request_execute callback 內直接執行 Command

Protocol v3 的 Agent 原本收到 `request_execute` 後，用 `run(delayMilliSeconds=1, delayRef=op.TDResources)` 把執行排到之後。鎖定版 TouchDesigner 2025.32050 的 frame 時戳探針（`tools/locked_frame_timing_acceptance.py`）量到三件事：

- SocketIO DAT 的 callback 在主 thread、每個 frame 開頭執行。
- `run()` 的 1 ms 延遲固定多等一個 frame（60 fps 下 16.3 ms），執行點同樣在 frame 開頭。
- `emit()` 立即送出，不會等到 frame 結束。

所以我們改成在 callback 內直接執行。執行的 thread 和 frame 內位置都和原本相同，只少等一個 frame。暫停 timeline 時 callback 照常執行，原本靠 `op.TDResources` 保證的性質仍然成立。

被拒絕的替代方案：

- `run(..., endFrame=True)`：callback 已在 frame 開頭，改到 frame 結束只會多等，不會更快。
- 為了 Daemon 端的速度把 SQLite 改成 `synchronous=NORMAL` 或減少寫入：Daemon 端每段只花 5–7 ms，大多被等下一個 frame 吸收；而 `docs/research/runtime-reliability-primary-sources.md` 要求保留 FULL。
