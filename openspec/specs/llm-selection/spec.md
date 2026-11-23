# llm-selection Specification

## Purpose
為「追蹤結果自然語言提問 agent」選定本機語言模型：用固定的工具、獨立算出的標準答案與預先訂好的門檻，在可重跑的評估中比較候選模型，並如實記錄結果。

## Requirements

### Requirement: The evaluation uses a fixed tool set and gold answers that are computed independently
評估 SHALL 只使用五個固定的工具：不同身分數（可選的「至少出現 N 張影格」過濾，預設不過濾）、某身分的第一張與最後一張影格、某張影格的人數、某身分出現的影格數、明確座標矩形內的身分清單（可選的影格範圍）。標準答案 SHALL 由一份獨立撰寫的程式直接逐行讀取 Result 檔產生，該程式 SHALL NOT 匯入或共用工具的任何程式碼。標準答案程式本身 SHALL 以至少三題手算的結果驗證。

#### Scenario: The gold-answer program shares no code with the tools
- **WHEN** 檢查標準答案程式的匯入
- **THEN** 它不匯入工具模組，工具模組也不匯入它，兩者各自從 Result 檔取得資料

#### Scenario: Three hand-computed answers match
- **WHEN** 以人工建構、答案一眼可知的小型序列執行標準答案程式
- **THEN** 至少三題的輸出與手算結果完全相同

#### Scenario: A region is judged by the bottom-centre point
- **WHEN** 問題給定矩形座標與影格範圍
- **THEN** 一個框只有在其底邊中心點落在矩形內時才算在區域內，標準答案與工具的結果相同

### Requirement: The question set has a fixed composition and is reproducible
題目 SHALL 約 30 題，由固定的種子與固定的 Result 檔產生，相同輸入 SHALL 得到相同題目與標準答案。題目 SHALL 以 MOT17-09-FRCNN 的真實 Result 為主，並加入一個小型合成序列，涵蓋空結果、只有一個身分、身分中斷後重現、同一影格大量身分。題目 SHALL 含可回答的題目與不可回答的題目；不可回答的題目 SHALL 涵蓋五類：影像內容、需要 Result 檔沒有的序列資訊（影像尺寸或幀率）、跨序列、預測、對應真實人物。

#### Scenario: Generation is deterministic
- **WHEN** 以同一個種子與同一個 Result 檔產生題目兩次
- **THEN** 兩份題目與標準答案逐位元組相同

#### Scenario: Every unanswerable category is present
- **WHEN** 檢查題目集
- **THEN** 五類不可回答的題目各至少有一題，且每題標明所屬類別

#### Scenario: Edge-case sequences are covered
- **WHEN** 檢查題目集
- **THEN** 合成序列的題目涵蓋空結果、單一身分、身分中斷後重現、同一影格大量身分

### Requirement: Each candidate is scored on four metrics over repeated trials without a single combined score
每個候選 SHALL 以溫度 0、固定的隨機種子，對每一題執行 3 次，所有次數都計入統計。評分 SHALL 回報四項指標：工具呼叫格式正確率（工具名稱屬於固定集合且參數通過型別與必填檢查）、答案正確率、拒答正確率（只計不可回答的題目：必須拒答並指出正確類別；對可回答的題目拒答則計入答案正確率，見下方情境）、延遲中位數。SHALL NOT 把它們合成單一分數。每次試驗的原始結果 SHALL 被保存。

#### Scenario: A malformed tool call is counted
- **WHEN** 模型呼叫不存在的工具，或缺少必填參數
- **THEN** 該次試驗的工具呼叫格式記為不正確，並保存原始回應

#### Scenario: A refusal with the wrong reason is not correct
- **WHEN** 模型對不可回答的題目拒答，但指出的類別與該題的類別不同
- **THEN** 該次試驗的拒答記為不正確

#### Scenario: Refusing an answerable question counts against answer correctness
- **WHEN** 模型對可回答的題目拒答
- **THEN** 該次試驗的答案記為不正確

#### Scenario: The model does not compute numbers itself
- **WHEN** 最終答案的數值與工具回傳的數值不同
- **THEN** 該次試驗在原始結果中標記為與工具輸出不一致（只記錄，不另列門檻）

### Requirement: Thresholds are fixed before evaluation and are never lowered
候選通過的條件 SHALL 是：答案正確率至少 90%、拒答正確率至少 90%、工具呼叫格式正確率至少 95%，且載入後顯存在共存上限內。這些門檻 SHALL 在看到任何評估結果之前寫入文件。沒有任何候選通過時，報告 SHALL 如實說明並 SHALL NOT 降低門檻；是否調整題目、門檻或做法 SHALL 由使用者決定。多個候選通過時，SHALL 選延遲中位數最低者；延遲相差在 10% 內時選顯存較小者；同時列出所有通過者。

#### Scenario: No candidate passes
- **WHEN** 所有候選都未達任一門檻
- **THEN** 報告列出每個候選未達標的指標與數值，結論寫明「沒有候選通過」，門檻不變

#### Scenario: Several candidates pass
- **WHEN** 兩個以上候選通過
- **THEN** 報告列出所有通過者，並依延遲與顯存的規則指出被選者

### Requirement: A model can co-reside with the worker only within the GPU memory budget
模型載入後的 GPU 記憶體 SHALL 以實測記錄，並與先實測的 worker（FP16）載入後記憶體增量一起寫進報告。可作為預設選項的模型 SHALL 在載入後約 16 GB 以內；超過者 SHALL 只列為資料，不得被選為預設。

#### Scenario: An oversized model is reported but not selected
- **WHEN** 某候選載入後超過 16 GB
- **THEN** 報告記錄它的指標，並註明因顯存不可與 worker 共存而不作為預設

### Requirement: Measurements are taken only while no other process is using the GPU
評估與 worker 顯存量測 SHALL 只在沒有其他程序使用 GPU 時進行：被評估的模型以外的 GPU 記憶體用量超過容許值（預設 2048 MiB）時，SHALL 暫停並輪詢等待，不得開始量測。每一題的三次試驗結束後 SHALL 再檢查一次；在量測期間有其他程序出現時，該題的試驗 SHALL 被丟棄並在 GPU 空閒後重做（最多三次，仍被干擾則評估失敗並回報）。無法讀取 GPU 記憶體時 SHALL 拒絕執行，等待超過上限時 SHALL 失敗並回報。摘要 SHALL 記錄等待次數、等待秒數與被丟棄的題目區塊數。

#### Scenario: The evaluation waits for another process to finish
- **WHEN** 開始評估時另一個程序佔用 GPU
- **THEN** 評估不送出任何問題，持續輪詢直到 GPU 空閒才開始，摘要記錄等待次數與秒數

#### Scenario: A disturbed block is measured again
- **WHEN** 某一題的三次試驗期間有其他程序開始使用 GPU
- **THEN** 該題的試驗不被計入統計，在 GPU 空閒後重做，摘要記錄被丟棄的區塊數

#### Scenario: The GPU state cannot be verified
- **WHEN** 無法讀取 GPU 記憶體
- **THEN** 評估拒絕開始並說明無法確認 GPU 是否空閒

### Requirement: Candidates are confirmed by the user before anything is pulled
候選模型 SHALL 先經查證（支援工具呼叫、本機可跑、授權清楚，優先 Apache 2.0 或 MIT），並向使用者列出來源、大小與授權及預計的下載總量。在使用者確認名單之前，SHALL NOT 拉取任何模型。

#### Scenario: Nothing is pulled before confirmation
- **WHEN** 候選名單尚未獲得使用者確認
- **THEN** 專屬卷內沒有任何模型，Docker 沒有新增任何 Ollama 以外的映像

### Requirement: The evaluation touches only dedicated resources and cleans up
評估 SHALL 只使用名稱以 `openmot-llm-eval` 開頭的容器與卷。評估前後 SHALL 比對 `docker ps -a`、`docker volume ls`、`docker images`，除專屬名稱的項目與 Ollama 映像外 SHALL 完全相同。結束後 SHALL 移除專屬容器、卷與拉取的模型，只保留記錄；要保留選定的模型 SHALL 先詢問使用者。評估 SHALL NOT 動使用者其他專案的環境、映像與 Hugging Face 快取。

#### Scenario: Other resources are unchanged
- **WHEN** 評估與清理完成
- **THEN** 前後清單的差異只包含 Ollama 映像（若使用者選擇保留則含選定的模型），專屬的容器與卷已不存在

### Requirement: Answers state their definitions and unanswerable questions are refused specifically
「不同身分數」預設 SHALL 回報原始身分數，過濾門檻只在題目明確給定時使用，答案 SHALL 註明所用的定義。區域 SHALL 只接受明確座標。不可回答的題目 SHALL 以具體的理由拒答（指出缺少什麼資訊或為何不在範圍），SHALL NOT 猜測數字。

#### Scenario: A relative region is refused
- **WHEN** 題目要求「左半邊」之類沒有座標的相對區域
- **THEN** 正確的回應是拒答並指出需要影像尺寸或明確座標

#### Scenario: Seconds cannot be derived from the result file
- **WHEN** 題目要求以秒為單位的停留時間
- **THEN** 正確的回應是拒答並指出結果檔沒有幀率，或改以影格數回答並明說單位

### Requirement: The selection report records the evidence and states what was not verified
`docs/llm-selection.md` SHALL 記錄每個候選的來源、授權、映像與模型摘要、大小、載入後顯存、四項指標、每次試驗的原始結果檔位置、失敗案例與原因、延遲、worker 的顯存增量、結論，以及限制（只涵蓋名單內的候選、題目只有一個真實序列與一個合成序列、結構化輸出可能掩蓋自由文字下的行為、沒有驗證雲端模型）。報告 SHALL 如實寫出沒有驗證的部分。

#### Scenario: The report lists what was not verified
- **WHEN** 閱讀報告的限制段
- **THEN** 它明說結論只適用於名單內的候選與這組題目，並列出上述未驗證項目
