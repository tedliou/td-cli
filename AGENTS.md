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

- 發版時維持 `td-agent upgrade-project` 的固定離線入口，依實際來源版本擴充明確 migration 白名單與冷啟／同版驗證；不得宣稱未驗證的任意跨版支援，也不得以一般 Command transport fallback 代替升級。

### Runtime reliability

- 每完成三個可獨立驗收的大階段及發版前，檢查本輪重複、過時路徑與耦合；依實際證據重構、簡化，完成回歸驗證及獨立 review，不累積臨時 fallback。
- 外部命令須有總逾時；輪詢須有截止時間，單次等待不超過 60 秒。逾時後查既有 Request outcome，不自動重送 mutation。

變更 Protocol、Daemon transport、RequestLifecycle、RequestStore、Agent callback／scheduler、
Socket.IO integration 或 locked TouchDesigner acceptance 時，必須先讀取並遵循
`.agents/skills/td-runtime-reliability/SKILL.md`。
