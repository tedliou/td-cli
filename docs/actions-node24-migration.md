# GitHub Actions Node.js 24 遷移

規格：[issue #138](https://github.com/tedliou/td-cli/issues/138)。日期：2026-09-16。

GitHub 在 2026-08-25 更新公告，預計於 **2026-09-23** 從 runner 移除 Node.js 20。
現有固定 SHA 的 checkout、setup-uv、upload-artifact 都宣告 `runs.using: node20`，
即使 runner 已強制使用 Node24，也應更新 action 本身。

## 固定版本與相容性裁決

| Action | 版本 | 完整 commit SHA |
| --- | --- | --- |
| actions/checkout | v7.0.1 | `3d3c42e5aac5ba805825da76410c181273ba90b1` |
| astral-sh/setup-uv | v10.1.0 | `bec219d24cd3e171d82865faccec33120bb574f4` |
| actions/upload-artifact | v7.0.1 | `043fb46d1a93c77aae656e7c1c64a875d1fc6a0a` |

三者官方 tag 已解析至上述 commit，對應 `action.yml` 皆使用 `node24`。
更新三份 workflow，保留 Windows runner、Python 3.11、uv 0.8.11、權限、觸發條件與憑證設定。
checkout 的 fork checkout 防護不影響本專案現有 `pull_request`／`push`／`workflow_dispatch` 事件。
setup-uv 明確設定 `prune-cache: true` 與 `download-from-astral-mirror: false`，
保留舊版的 cache pruning 與 GitHub Releases 下載來源。
upload-artifact 明確使用 `archive: true`，保持 ZIP、artifact-id、artifact-digest 的既有介面。

## 驗證與界線

執行完整本機 gate，獨立檢視 Standards／Spec，並以 Windows CI 驗證 checkout、
setup-uv、測試、Python 套件打包與三個 executable smoke。
CI 完成後檢查 annotations 與 logs，確認沒有 Node20 deprecation 訊息。
正式 Agent staging 與 Release 發布不在本輪執行範圍；其 action 介面採靜態核對。
透過 `develop → main` PR 流程合併，版本維持 0.7.1，不建立 tag、不發版。

本機完整 gate：522 tests passed；ruff check／format、mypy、uv lock check、
授權快照檢查與 Agent source inspection 均通過。既有一項 Starlette/httpx deprecation warning。
Standards 與 Spec 獨立審查均為 0 項發現；Standards 審查者另直接核對三個 exact SHA
的上游 action.yml 與現有 inputs/outputs。
版本註解置於 `uses` 前一行，保持既有 workflow SHA 契約檢查不變。
實際 Windows CI 與 annotations/logs 驗收結果記錄於
[實作 PR #139](https://github.com/tedliou/td-cli/pull/139)。

## 官方來源

- [Node20 移除公告](https://github.blog/changelog/2025-09-19-deprecation-of-node-20-on-github-actions-runners/)
- [checkout v7.0.1](https://github.com/actions/checkout/releases/tag/v7.0.1)
- [setup-uv v10.1.0](https://github.com/astral-sh/setup-uv/releases/tag/v10.1.0)
- [upload-artifact v7.0.1](https://github.com/actions/upload-artifact/releases/tag/v7.0.1)
