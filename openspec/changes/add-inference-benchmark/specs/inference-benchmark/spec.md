# inference-benchmark Specification

## Purpose
以固定、可重現的方式量測偵測器在單張影格上的延遲與吞吐，並記錄足以公平比較的環境資訊，作為評估各種推論後端與量化設定的共同基準。

## ADDED Requirements
### Requirement: A latency benchmark is reproducible with one command
系統 SHALL 提供單一指令，從已下載的 MOT17 資料出發，以 batch 1 逐張量測偵測器，並寫出量測結果。

#### Scenario: Data and GPU are present
- **WHEN** 使用者在 MOT17 已下載且 GPU 可用的情況下執行量測
- **THEN** 指令完成預熱與量測，寫出含各項數字的結果檔，並以結束碼 0 結束

#### Scenario: Data is missing
- **WHEN** 使用者在 MOT17 尚未下載時執行量測
- **THEN** 指令以非 0 的結束碼結束，並提示使用 `data/download_mot.sh` 取得資料

#### Scenario: Environment check fails
- **WHEN** 環境檢查未通過（例如 CUDA 不可用）
- **THEN** 指令以非 0 的結束碼結束並說明原因，不寫出任何結果檔

### Requirement: Measured frames come from the Validation Half
量測用的影格 SHALL 取自各 Sequence 的 Validation Half，依序使用；影格數不足時循環使用。影格 SHALL 在計時開始前全部讀入記憶體，使磁碟讀取與影像解碼不計入延遲。

#### Scenario: More measurements than available frames
- **WHEN** 要求的預熱加量測張數超過可用的 Validation Half 影格總數
- **THEN** 影格從頭循環使用，指令仍完成量測

#### Scenario: Decoding is excluded
- **WHEN** 量測進行中
- **THEN** 每張影格的計時區間不包含讀檔與影像解碼

### Requirement: Warm-up frames are excluded from the results
系統 SHALL 在量測之前先處理一批預熱影格，預熱的耗時 SHALL NOT 計入任何回報的數字。預熱與量測的張數預設為 50 與 500，且 SHALL 可由使用者指定；兩者必須為正整數。

#### Scenario: Default counts
- **WHEN** 使用者未指定張數
- **THEN** 結果記錄預熱 50 張、量測 500 張，且統計只含這 500 張

#### Scenario: Invalid count
- **WHEN** 使用者指定的預熱或量測張數不是正整數
- **THEN** 指令以非 0 的結束碼結束並說明哪個參數無效，不載入模型

### Requirement: Latency is reported at two layers
系統 SHALL 分別回報「純推論」與「偵測端到端」兩層延遲，各以中位數與 p95 表示，單位為毫秒。純推論 SHALL 只含推論引擎的執行；偵測端到端 SHALL 含前處理、推論與後處理（解碼與 NMS）。追蹤器 SHALL NOT 計入。計時 SHALL 在 GPU 工作完成之後才停止，使數字反映實際完成時間而非工作排入佇列的時間。

#### Scenario: Both layers are reported
- **WHEN** 一次量測完成
- **THEN** 結果含純推論與偵測端到端各自的中位數與 p95，且端到端的中位數不小於純推論的中位數

#### Scenario: Asynchronous GPU work
- **WHEN** 推論在 GPU 上以非同步方式執行
- **THEN** 回報的延遲包含 GPU 完成工作所需的時間

### Requirement: Throughput is reported for a single stream
系統 SHALL 回報單串流、batch 1 的吞吐（每秒影格數），以量測的影格數除以偵測端到端的延遲總和計算，不含影格之間的空檔。

#### Scenario: Throughput matches latency
- **WHEN** 一次量測完成
- **THEN** 回報的吞吐等於量測張數除以各張端到端延遲的總和（秒）

### Requirement: The result records how it was produced
量測結果 SHALL 記錄推論後端、推論精度、偵測輸入尺寸、模型權重的識別資訊、預熱與量測張數、軟體版本、GPU 型號與驅動版本，使不同次的結果可以被公平比較。結果 SHALL 以機器可讀的檔案與人類可讀的表格兩種形式寫出，並 SHALL 寫入被版本控制忽略的位置。

#### Scenario: Result metadata
- **WHEN** 一次量測完成
- **THEN** 結果檔含上述全部中繼資料，且與該次實際使用的設定一致

#### Scenario: Result folder already exists
- **WHEN** 使用者指定的結果資料夾已存在
- **THEN** 指令以非 0 的結束碼結束，不覆蓋既有結果

#### Scenario: Running the benchmark twice
- **WHEN** 使用者連續執行兩次量測
- **THEN** 原始 MOT17 資料保持不變，且 `git status` 不出現任何新的未追蹤資料檔
