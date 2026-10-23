# CLI 啟動時間驗收

日期：2026-10-24。Issue: https://github.com/tedliou/td-cli/issues/163 。

## 根因

- `td` 以 PyInstaller onefile 打包，每次執行都要先把整個 runtime 解壓到暫存目錄。
- `td_cli.client` 在 import 時就載入 `td_cli.daemon.cli`，連帶載入 CLI 用不到的 Daemon 伺服器套件（fastapi、uvicorn、socketio），約 0.55 秒。

## 變更

- `td_cli.client.ensure_running` 只在需要啟動 Daemon 時才 import Daemon。`ENDPOINT` 移到 `td_cli.daemon.runtime_files`。`test_cli_startup_does_not_import_the_daemon_server_stack` 會守住「import CLI 時不載入 Daemon 伺服器套件」這件事。
- `td` 改用 onedir 打包：`versions\<版本>\td.exe` 與 `td-runtime\` 並存。`td-daemon`、`td-agent` 不會頻繁啟動，維持 onefile。release zip 會收錄 `td-runtime\` 下的所有檔案，`install.ps1` 照常逐檔驗證。
- 指令的輸入與輸出不變。

## 量測（同一台機器，PowerShell `Measure-Command`）

每個指令各量 3–5 次，例如：`1..5 | % { (Measure-Command { td --version | Out-Null }).TotalSeconds }`。Daemon 已在執行，所以表中不包含首次自動啟動 Daemon 的時間；`td-daemon` 仍是 onefile，自動啟動時照舊要付一次解壓成本。

| 指令 | 0.10.0（onefile） | 本分支（onedir，經 `install.ps1` 安裝） |
| --- | --- | --- |
| `td --version` | 2.02–2.24 秒 | 0.43–0.44 秒 |
| `td --json instances list`（實際送 Request 給 Daemon） | 2.50–2.56 秒 | 0.86–0.88 秒 |

建置與安裝流程：`pyinstaller packaging/td.spec` → `package_release` → `install.ps1`（PowerShell 7）首次安裝成功；重複執行時回報 `already installed and verified`。
