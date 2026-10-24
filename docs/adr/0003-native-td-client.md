---
Status: accepted
---

# `td` 改寫成 Rust 單一執行檔，Python 仍是 catalog 與驗證的權威

P1 之後，暖機的 `td --json instances list` 要 317 ms，`td --version` 也要 306 ms。時間幾乎都花在凍結版 Python 的啟動與匯入；Daemon 回應只要幾 ms。用原生程式（編譯成機器碼、不需要直譯器的程式）重寫 `td`，每次命令只剩行程啟動與一次本機 HTTP 往返，目標是 50 ms 以內。

決定：

- `td` 用 Rust 寫成一個 exe。`td-daemon`、`td-agent` 仍是 Python。Agent 與 Daemon 之間的 Protocol v3 訊息不變；Daemon 的 HTTP 介面只新增可選欄位與端點，舊 client 照常可用。
- Python 仍是 Command catalog、operator catalog 與錯誤目錄的唯一來源。建置時把它們匯出成 JSON 嵌進 exe，測試確保匯出檔與 Python 一致。
- Command 驗證移到 Daemon。`POST /v3/requests` 可改帶 Instance selector，Daemon 依序驗證 Command、解析 selector、收件，錯誤碼、細節與優先順序和現在的 client 相同。這樣 Rust 不必重做 pydantic 驗證，selector 規則也只有一份。
- Daemon 不在時，`td` 呼叫同目錄的 `td-daemon.exe start`，沿用既有的啟動與健康檢查，只在冷啟動多付 Python 啟動的時間。
- `--json` 信封、人看的輸出、stderr 的錯誤碼行與 exit code 逐字不變。數字照 Daemon 回應的原文輸出，非 ASCII 字元照 Python `ensure_ascii` 的規則跳脫。`--help` 的命令與選項相同，排版可以不同。
- 等價移植完成後刪掉 Python 版 `td`，不保留 Python fallback。

被拒絕的替代方案：

- 把 Python CLI 改成極簡匯入：實測下限約 90 ms，仍是原生的好幾倍，而且每加一個命令就要再守一次匯入預算。
- Rust 依 JSON Schema 自己驗證 Command：錯誤細節的格式會和 pydantic 不同，等於有兩套驗證規則要對齊。
- 把等待併進 POST：POST 不能重試，等待中斷線就無法安全重查；保留「POST 收件、GET 長輪詢」只多一次 1 ms 的往返。
