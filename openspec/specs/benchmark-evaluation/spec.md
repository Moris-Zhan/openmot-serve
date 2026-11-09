# benchmark-evaluation Specification

## Purpose
以固定、可重現且不洩漏答案的方式，在 MOT17 上量測整條追蹤管線的品質，產出 HOTA、MOTA、IDF1，作為本專案所有後續優化（例如 INT8 量化）的比較基準。

## Requirements
### Requirement: Each training Sequence is split into two non-overlapping halves
系統 SHALL 將每個 MOT17 訓練 Sequence 切成 Tuning Half 與 Validation Half：長度為 N 的 Sequence，Tuning Half 為第 1 到 floor(N/2) 個 Frame，Validation Half 為其餘的 Frame。兩者不得重疊，且合起來涵蓋所有 Frame。

#### Scenario: Even-length Sequence
- **WHEN** 一個 Sequence 共有 600 個 Frame
- **THEN** Tuning Half 為第 1 到 300 個 Frame，Validation Half 為第 301 到 600 個 Frame

#### Scenario: Odd-length Sequence
- **WHEN** 一個 Sequence 共有 525 個 Frame
- **THEN** Tuning Half 為第 1 到 262 個 Frame，Validation Half 為第 263 到 525 個 Frame

### Requirement: Parameter tuning uses only the Tuning Half
選擇追蹤參數的過程 SHALL 只讀取並評分 Tuning Half，不得讀取 Validation Half 的影格或標註。

#### Scenario: Tuning run
- **WHEN** 使用者執行參數調校
- **THEN** 調校只在 Tuning Half 上產生分數，並輸出被選中的參數組合

#### Scenario: Validation data stays untouched during tuning
- **WHEN** 參數調校執行期間
- **THEN** 系統不讀取任何屬於 Validation Half 的影格或標註

### Requirement: Reported scores come from the Validation Half only
對外報告的 HOTA、MOTA、IDF1 SHALL 僅以 Validation Half 計算，並使用已確定的參數；Result 中落在 Validation Half 之外的 Frame 不得計入。

#### Scenario: Benchmark Run output
- **WHEN** 使用者完成一次 Benchmark Run
- **THEN** 輸出包含每個 Sequence 與全部合併的 HOTA、MOTA、IDF1

#### Scenario: Frames outside the Validation Half
- **WHEN** Result 含有 Validation Half 以外的 Frame
- **THEN** 這些 Frame 不影響任何分數

### Requirement: Scoring follows the MOT17 pedestrian convention
計分 SHALL 遵循 MOT17 的行人評測慣例：只評估行人類別，並依官方規則忽略被標為不列入評分的標註與干擾物。

#### Scenario: Ground truth marked as not considered
- **WHEN** 標註中含有被標為不列入評分的物件
- **THEN** 偵測到該物件不被算作誤報，沒偵測到也不被算作漏報

### Requirement: A Benchmark Run is reproducible with one command
系統 SHALL 提供單一指令，從已下載的 MOT17 資料出發，依序完成切分、追蹤與計分，並寫出分數摘要。

#### Scenario: Data is present
- **WHEN** 使用者在 MOT17 已下載的情況下執行 Benchmark Run
- **THEN** 指令完成全部步驟並寫出包含各項分數的摘要檔

#### Scenario: Data is missing
- **WHEN** 使用者在 MOT17 尚未下載時執行 Benchmark Run
- **THEN** 指令以非 0 的結束碼結束，並提示使用 `data/download_mot.sh` 取得資料

### Requirement: The score summary records how it was produced
分數摘要 SHALL 記錄偵測模型權重的識別資訊、推論精度（例如 FP16）、最終追蹤參數、軟體版本與 GPU 型號，使不同次的結果可以被公平比較。

#### Scenario: Summary metadata
- **WHEN** 一次 Benchmark Run 完成
- **THEN** 摘要檔含有上述全部中繼資料，且與該次實際使用的設定一致

### Requirement: Derived data stays out of version control
Benchmark Run 產生的切分資料與結果 SHALL 寫入被版本控制忽略的位置，且不得修改原始下載的 MOT17 資料。

#### Scenario: Running a benchmark twice
- **WHEN** 使用者連續執行兩次 Benchmark Run
- **THEN** 原始 MOT17 資料保持不變，且 `git status` 不出現任何新的未追蹤資料檔
