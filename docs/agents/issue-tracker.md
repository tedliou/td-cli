# Issue tracker: GitHub

本 repo 的 issue 與規格以 GitHub Issues 管理。所有操作請使用 `gh` CLI。

## 慣例

- 建立 issue：`gh issue create --title "..." --body "..."`
- 讀取 issue：`gh issue view <number> --comments`
- 列出 issue：`gh issue list --state open`
- 留言：`gh issue comment <number> --body "..."`
- 新增／移除標籤：`gh issue edit <number> --add-label "..."` ／ `--remove-label "..."`
- 關閉：`gh issue close <number> --comment "..."`

在此 clone 內執行時，`gh` 會依 git remote 自動推斷 repo。

## Pull requests as a triage surface

**PRs as a request surface: no.**

若日後改為 `yes`，外部 PR 會和 issue 共用 triage 流程與標籤，並使用對應的 `gh pr` 指令。

## 當技能要求「publish to the issue tracker」

建立一個 GitHub issue。

## 當技能要求「fetch the relevant ticket」

執行 `gh issue view <number> --comments`。

## Wayfinding operations

`/wayfinder` 的 map 是一張標記為 `wayfinder:map` 的 GitHub issue，並以 GitHub sub-issue（無法使用時則以 task list）連結子工作。

- 子工作使用 `wayfinder:<type>` 標籤，類型為 `research`、`prototype`、`grilling` 或 `task`。
- 阻擋關係優先使用 GitHub 原生 issue dependencies；不可用時，以 issue 內的 `Blocked by: #<n>` 記錄。
- 可執行的下一項工作是：map 的未關閉子工作中，沒有未關閉阻擋項目且尚未指派者。
- 認領時：`gh issue edit <n> --add-assignee @me`。
- 完成時：留言、關閉 issue，並將脈絡指標補到 map 的 Decisions-so-far。
