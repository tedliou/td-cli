# Constant CHOP Sequence 驗收

日期：2026-09-13。Issue: https://github.com/tedliou/td-cli/issues/129 。

## 根因與介面

TouchDesigner 2025.32050 的 Constant CHOP const Sequence blockSize=1；SequenceBlock 迭代卻回傳兩次同一個 const0name ParGroup，每個群組都包含 const0name / const0value。Agent 原本重複序列化，導致正常的 name/value 替換在 preflight 被拒絕。

每個 block 依實際 Parameter.name 保留首次出現，維持順序。型別、唯一參數數量上限、preflight、完整讀回與 rollback 路徑不變；未加入新 Command、transport 或重送策略。

官方語義：[SequenceBlock](https://docs.derivative.ca/SequenceBlock_Class)、[Sequence](https://docs.derivative.ca/Sequence_Class)、[ParGroup](https://docs.derivative.ca/ParGroup_Class)，查閱 2026-09-13。官方迭代說明不足以預見此別名重複，故以 locked runtime 證據裁決。

## 原作品重現

E:/collective-dream-factory/CollectiveDreamFactory.toe，Agent 0.7.0，Selector c098，PID 13044。讀取 Request 01a09a04-8262-78b8-915f-ec023533e74b；替換 Request 01a09a03-d346-7823-8a7e-8f92a173b82e 明確失敗 parameter_sequence_shape_invalid，無未知 mutation。

## 隔離 locked 驗收

使用無 Agent/Socket 的 disposable Execute DAT project，不連共用 Daemon、不修改作品。診斷 PID 24084、正式 handler 驗收 PID 4576 均自行退出。`tools/locked_constant_sequence_probe.py` 對實際 TD objects 呼叫公開 Command handler `OperatorControl.execute`。

- 預設 get 僅 name/value 各一次，max_parameters=2 成功。
- 1→3 block replace 成功，保留 constant/expression 模式及完整讀回。
- expression 來源為 `1 + 2`，TD 值為 3。
- 缺欄位的替換由 preflight 拒絕，原狀態不變。此項不是注入寫入中失敗的 rollback 驗收。
- 原始 shape 及 exact source SHA-256 詳見 [runtime-shape.json](evidence/constant-sequence/runtime-shape.json) 與 [acceptance.json](evidence/constant-sequence/acceptance.json)。

## 自動測試與審查

新增公開 handler 回歸測試先失敗於重複欄位超界，再通過。完整 pytest 509 passed；ruff check、format、mypy src、uv lock --check、inspect-source、git diff --check 全通過。獨立第三方 review 未發現實質問題。

## 限制與簡化

此階段證明 source handler 的原生 TD 行為；尚未表示新版 artifact 已發布、安裝或原作品 Agent 已升級。部署驗收另行記錄。單一 block 區域 set 即可收斂 vendor 別名，未新增抽象或重複路径。

測試啟動曾發現 vendor toecollapse 遇 CRLF toc 會印警告、產生 0-byte、仍 exit 0；修正為 LF 並驗證非零產物才啟動。失敗測試 PID 已辨識並關閉，作品未受影響。
