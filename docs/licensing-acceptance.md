# MIT 與第三方授權驗收

日期：2026-09-16。規格 [#135](https://github.com/tedliou/td-cli/issues/135)，
實作 PR [#136](https://github.com/tedliou/td-cli/pull/136)。
基準：`598036dcb3ef27fe5facf42926c1364f72260199`。
產品實作：`18b47d6`、`676a2a7`；本文件與第三方清單表格排版為後續文件修正。

## 本機結果

- 完整 gate：`uv run pytest -q` 為 **522 passed**。
- `uv run ruff check .`、`uv run ruff format --check .`、`uv run mypy src`、
  `uv lock --check`、`uv run python -m td_cli.agent_tool inspect-source agent`、
  `git diff --check` 均通過。
- `uv run python scripts/check_licenses.py --runtime` 驗證 **272 份文件**，
  inventory 記錄 **179 個元件項目**（工具鏈聯集，不等同單一執行檔的載入元件數）。
- `uv build --out-dir build/license-python-packages` 實際建置
  `touchdesigner_cli-0.7.1-py3-none-any.whl` 與 `touchdesigner_cli-0.7.1.tar.gz`。
  `uv run python scripts/check_licenses.py --python-artifacts build/license-python-packages`
  核對 MIT metadata、完整 License-File 集合與全部原文 bytes。
- `package_release` 整合測試核對四個 ZIP 的授權內容、穩定 checksum、
  授權缺檔／變造／鎖檔漂移拒絕；未知原生版本也會被拒絕。
- 本機 HTTP＋實際 PowerShell 安裝測試證明：首次安裝及同版驗證成功，
  修改或刪除 `LICENSES/` 內文件後，同版驗證失敗。

## 執行環境與來源核對

| 環境 | Python | OpenSSL | SQLite | zlib | Expat |
| --- | --- | --- | --- | --- | --- |
| 既有 GitHub Windows runner | 3.11.9 | 3.0.13 | 3.45.1 | 1.3.1 | 2.6.0 |
| 本機 uv Windows runtime | 3.11.15 | 3.5.6 | 3.53.1 | 1.3.1 | 2.7.4 |

初次 CI 的 runtime guard 正確拒絕未收錄的 3.11.9，沒有放寬檢查。
取 Python.org 的官方 `python-3.11.9-embed-amd64.zip` 實際執行同一 checker，
確認版本與 LICENSE；既有 CI 工具鏈保持不變。兩組環境均通過核對。

本機重新執行三份 `packaging/*.spec`，PyInstaller 建置成功。
檢查三份新 `Analysis-00.toc`，所有 site-packages 來源均能對應到已收錄的 distribution，
沒有未歸屬來源；另檢查 setuptools 內嵌授權及 pydantic-core SBOM。
Rust crate 下載逐一符合 wheel SBOM 提供的 SHA-256。原文 SHA-256、來源與原路徑保留於 inventory。
本機調查建置不是不可變 Release；最終 commit 的 executable smoke 由 PR CI 執行。

## 獨立審查與界線

- Standards：0 項重大發現；補審指出一處 Markdown 表格空白行，已修正。
- Spec：0 項缺陷；runtime variants 補審亦無必要修正。
- 審查者檢視程式碼與文件，未獨立重下載每份第三方原文；上游下載及 artifact bytes 驗證由主 session 執行。
- 測試有一項既有 Starlette／httpx deprecation warning，無測試失敗。
- ZIP 與安裝測試使用合成 Agent stage；未執行 locked TouchDesigner 驗收，
  因本輪未修改 Agent、Protocol、runtime 命令或 migration。
- 不修改 `0.7.1` 版本、不建立 tag、不發版、不回寫既有 Release。
  最終 CI 與 `develop → main` 合併證據記錄於 PR。
