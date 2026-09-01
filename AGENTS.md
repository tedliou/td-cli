# Agent

## Agent skills

- 一律使用正體中文與使用者溝通。

### Issue tracker

Issue 與規格均使用 GitHub Issues 管理。詳見 `docs/agents/issue-tracker.md`。

### Triage labels

使用預設的五種 triage 標籤。詳見 `docs/agents/triage-labels.md`。

### Domain docs

採用單一領域脈絡（single-context）配置。詳見 `docs/agents/domain.md`。

### Agent-led development

- 對已授權的端到端開發，主 session 負責規格、grilling 決策、tickets、實作、驗證與 PR；使用者只在完整 PR 交付後審核。
- Grilling 是代理內部設計審查。主 session 依專案證據與權威文件作最終決策，不把可自行查明或裁決的問題退回使用者。
- 子代理只用於技能明確要求、grilling，或需要獨立第三方 review；主控制與產品實作留在主 session。
- 每個階段開始前先提交既有變更並確認工作區乾淨；每輪產品變更使用獨立分支。

### Runtime reliability

變更 Protocol、Daemon transport、RequestLifecycle、RequestStore、Agent callback／scheduler、
Socket.IO integration 或 locked TouchDesigner acceptance 時，必須先讀取並遵循
`.agents/skills/td-runtime-reliability/SKILL.md`。
