# sequence-tracking Specification

## Purpose
將一個 Sequence 中的人物偵測並追蹤成帶有持續 Identity 的 Track，並以 MOTChallenge 格式輸出 Result，作為評測與後續服務化的共同基礎。

## Requirements
### Requirement: Track a Sequence given as an ordered frame folder
系統 SHALL 接受以有序影格資料夾表示的 Sequence，依 1 起算的 Frame 編號順序逐格處理，並為整個 Sequence 產生一份 Result。

#### Scenario: Valid frame folder
- **WHEN** 使用者對包含連續編號影格的資料夾執行追蹤
- **THEN** 系統依編號順序處理每一個 Frame，並寫出該 Sequence 的 Result 檔

#### Scenario: Empty or unreadable input
- **WHEN** 輸入資料夾不存在、沒有影格，或其中有影格無法讀取
- **THEN** 系統回報錯誤並以非 0 的結束碼結束，且不留下不完整的 Result 檔

### Requirement: Result is written in MOTChallenge format
Result 的每一行 SHALL 對應一個被指派到 Track 的 Detection，格式為 `frame,id,bb_left,bb_top,bb_width,bb_height,conf,-1,-1,-1`，其中 Frame 編號從 1 起算，Identity 為正整數。

#### Scenario: Result line layout
- **WHEN** 系統完成一個 Sequence 的追蹤
- **THEN** Result 檔每一行有 10 個以逗號分隔的欄位，且前兩欄為正整數的 Frame 編號與 Identity

#### Scenario: Evaluator accepts the file
- **WHEN** 以 MOTChallenge 評測工具讀取該 Result 檔
- **THEN** 評測工具不因格式問題而拒絕或忽略任何一行

### Requirement: Only people are tracked
系統 SHALL 只輸出人物類別的 Detection，其他物件類別（例如車輛）不得出現在 Result 中。

#### Scenario: Non-person objects in the scene
- **WHEN** 影格中同時有人物與車輛
- **THEN** Result 只包含人物的 Detection

### Requirement: Identities persist and are never reused
系統 SHALL 讓同一個物件在持續被追蹤的期間保有同一個 Identity，且在同一個 Sequence 內不得把已使用過的 Identity 指派給不同的物件。

#### Scenario: Object keeps its Identity across frames
- **WHEN** 一個物件連續出現在多個 Frame 中且每格都有 Detection
- **THEN** 這些 Detection 在 Result 中使用同一個 Identity

#### Scenario: Short occlusion within the retention window
- **WHEN** 一個物件短暫消失的幀數不超過設定的 Track 保留幀數後又重新出現
- **THEN** 重新出現的 Detection 沿用原本的 Identity

#### Scenario: Lost Identity is not reused
- **WHEN** 一個 Track 因超過保留幀數而終止，之後出現新的物件
- **THEN** 新物件取得從未在該 Sequence 中使用過的 Identity

### Requirement: Association is deterministic
對相同的 Detection 序列與相同的追蹤參數，追蹤階段 SHALL 產生完全相同的 Result，不受執行次數影響。

#### Scenario: Same detections, same result
- **WHEN** 以相同的 Detection 序列與相同參數執行追蹤兩次
- **THEN** 兩次產生的 Result 檔內容逐位元組相同

### Requirement: Tracking parameters are configurable and recorded
系統 SHALL 允許使用者指定偵測信心門檻、匹配門檻與 Track 保留幀數，並將實際使用的參數、偵測模型權重的識別資訊與推論精度寫入與 Result 並列的中繼檔。

#### Scenario: Custom parameters
- **WHEN** 使用者以設定檔指定不同的偵測信心門檻
- **THEN** 追蹤使用該門檻，且中繼檔記錄該值

#### Scenario: Default parameters
- **WHEN** 使用者未指定任何追蹤參數
- **THEN** 系統使用預設值，且中繼檔記錄實際使用的預設值
