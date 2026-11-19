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
計分 SHALL 遵循 MOTChallenge 的行人評測慣例：只評估行人類別，並依官方規則忽略被標為不列入評分的標註與干擾物。評測 MOT20 時，計分 SHALL 以 MOT20 的慣例執行，使 `non_mot_vehicle` 類別的標註與其他干擾物同樣被忽略；評測 MOT17 時維持 MOT17 的慣例，不因這個變更而改變。

#### Scenario: Ground truth marked as not considered
- **WHEN** 標註中含有被標為不列入評分的物件
- **THEN** 偵測到該物件不被算作誤報，沒偵測到也不被算作漏報

#### Scenario: MOT20 non-MOT vehicles are ignored
- **WHEN** 以 MOT20 計分，標註中含有 `non_mot_vehicle` 類別的物件，而追蹤結果在該物件上有一個框
- **THEN** 該框不被算作誤報；同樣的資料以 MOT17 計分時則被算作誤報

### Requirement: A Benchmark Run is reproducible with one command
系統 SHALL 提供單一指令，從已下載的 MOT17 資料出發，依序完成切分、追蹤與計分，並寫出分數摘要。

#### Scenario: Data is present
- **WHEN** 使用者在 MOT17 已下載的情況下執行 Benchmark Run
- **THEN** 指令完成全部步驟並寫出包含各項分數的摘要檔

#### Scenario: Data is missing
- **WHEN** 使用者在 MOT17 尚未下載時執行 Benchmark Run
- **THEN** 指令以非 0 的結束碼結束，並提示使用 `data/download_mot.sh` 取得資料

### Requirement: The score summary records how it was produced
分數摘要 SHALL 記錄偵測模型權重的識別資訊、推論後端、推論精度（例如 FP16）、所評測的 benchmark、最終追蹤參數、軟體版本與 GPU 型號，使不同次的結果可以被公平比較。略過調參而直接使用參數檔時，摘要 SHALL 註明參數來自參數檔且未調參，並記錄使用者給的參數檔路徑，使讀者不會把數字誤讀成針對該 benchmark 調過參。

#### Scenario: Summary metadata
- **WHEN** 一次 Benchmark Run 完成
- **THEN** 摘要檔含有上述全部中繼資料，且與該次實際使用的設定一致

#### Scenario: Parameters were not tuned for this benchmark
- **WHEN** 一次 Benchmark Run 以參數檔完成
- **THEN** 摘要記錄 benchmark、參數檔的路徑，並註明未調參

### Requirement: Derived data stays out of version control
Benchmark Run 產生的切分資料與結果 SHALL 寫入被版本控制忽略的位置，且不得修改原始下載的 MOT17 資料。

#### Scenario: Running a benchmark twice
- **WHEN** 使用者連續執行兩次 Benchmark Run
- **THEN** 原始 MOT17 資料保持不變，且 `git status` 不出現任何新的未追蹤資料檔

### Requirement: Cached detections are never shared between inference backends
偵測快取 SHALL 將推論後端納入其識別依據：以不同後端產生的偵測結果 SHALL NOT 互相沿用，即使其他設定（權重、精度、輸入尺寸、門檻）完全相同。

#### Scenario: Same settings, different backend
- **WHEN** 兩次執行除了推論後端之外的設定都相同
- **THEN** 第二次執行不使用第一次的快取，而是重新偵測

#### Scenario: Same settings, same backend
- **WHEN** 兩次執行的所有設定（含推論後端）都相同
- **THEN** 第二次執行直接使用快取，不重新偵測

### Requirement: A Benchmark Run can select the inference backend
Benchmark Run 的指令 SHALL 接受推論後端與（可選的）產物路徑；未指定時使用 `torch` 後端與既有的預設行為。不支援的後端、不支援的後端與精度組合，或產物驗證失敗，SHALL 使指令以非 0 的結束碼結束並說明原因，且 SHALL 在任何偵測開始之前發生，不寫出結果資料夾。分數摘要 SHALL 記錄實際使用的後端；使用 ONNX 或 TensorRT 後端時，摘要 SHALL 另記錄所用產物的識別資訊（檔名與 sidecar 中的 ONNX sha256，TensorRT 另含函式庫版本與 GPU 型號，INT8 engine 另含校準的張數、挑選清單與校準快取的 sha256）。

#### Scenario: Default backend is unchanged
- **WHEN** 使用者未指定後端而執行 Benchmark Run
- **THEN** 指令使用 `torch` 後端，分數與先前相同，摘要記錄後端為 `torch`

#### Scenario: Selecting an exported backend
- **WHEN** 使用者指定 `tensorrt` 與 `fp16` 並已建好對應的 engine
- **THEN** 指令使用該 engine 完成 Benchmark Run，摘要記錄後端為 `tensorrt` 與所用 engine 的識別資訊

#### Scenario: Selecting an INT8 engine
- **WHEN** 使用者指定 `tensorrt` 與 `int8` 並已建好對應的 INT8 engine
- **THEN** 指令使用該 engine 完成 Benchmark Run，摘要記錄精度為 `int8` 與該 engine 的校準識別資訊；偵測快取的鍵含精度，不會沿用 FP16 的結果

#### Scenario: Unsupported combination is rejected early
- **WHEN** 使用者指定 `onnx` 搭配 `fp16`
- **THEN** 指令以非 0 的結束碼結束並說明 `onnx` 只支援 `fp32`，沒有載入模型、沒有偵測，也沒有寫出結果資料夾

#### Scenario: Artifact does not match
- **WHEN** 指定後端的產物未通過驗證（例如 sidecar 記錄的權重 sha256 與目前不同）
- **THEN** 指令以非 0 的結束碼結束並指名不符的項目，不寫出結果資料夾

### Requirement: A Benchmark Run can target MOT20
Benchmark Run 的指令 SHALL 接受 benchmark 的選擇（`MOT17` 或 `MOT20`），未指定時為 `MOT17`，且未指定時的行為與提供這個選項之前相同。選擇 `MOT20` 時，預設的資料根目錄與預設序列 SHALL 指向 MOT20 的訓練序列，明確指定的資料根目錄與序列 SHALL 優先，且 MOT20 SHALL 與 MOT17 使用同一套切分、Validation Half 計分與結果格式。找不到所選 benchmark 的序列時，指令 SHALL 以非 0 的結束碼結束，訊息指名所選的 benchmark 與缺少的序列，並提示使用 `data/download_mot.sh`（MOT20 另提示 `--with-mot20`）。

#### Scenario: Default benchmark is unchanged
- **WHEN** 使用者未指定 benchmark 而執行 Benchmark Run
- **THEN** 指令使用 MOT17 的預設資料根目錄與七個 FRCNN 序列，分數與先前相同

#### Scenario: Selecting MOT20
- **WHEN** 使用者指定 `MOT20` 且 MOT20 已下載
- **THEN** 指令以 MOT20 的預設序列完成切分、追蹤與 Validation Half 計分，並寫出每個序列與合併的 HOTA、MOTA、IDF1

#### Scenario: MOT20 is not downloaded
- **WHEN** 使用者指定 `MOT20` 但 MOT20 尚未下載
- **THEN** 指令以非 0 的結束碼結束，訊息指名 MOT20 與缺少的序列，並提示 `data/download_mot.sh --with-mot20`

### Requirement: A Benchmark Run can reuse existing tracker parameters
Benchmark Run 的指令 SHALL 接受一個既有的追蹤器參數檔。指定時 SHALL 直接使用該參數、略過 Tuning Half 的調參，且 SHALL NOT 讀取或偵測任何屬於 Tuning Half 的影格；未指定時 SHALL 照原本流程在 Tuning Half 調參。參數檔不存在或內容無效 SHALL 使指令在任何偵測開始之前以非 0 的結束碼結束並說明原因，不寫出結果資料夾。

#### Scenario: Reusing tracker parameters
- **WHEN** 使用者指定 `MOT20` 並提供 MOT17 那次執行寫出的參數檔
- **THEN** 指令使用該參數、不執行調參，不讀取 Tuning Half 的影格，並以 Validation Half 計分

#### Scenario: Tuning is unchanged without a parameter file
- **WHEN** 使用者沒有提供參數檔
- **THEN** 指令在 Tuning Half 調參，流程與先前相同

#### Scenario: Unusable parameter file
- **WHEN** 使用者提供不存在或內容無效的參數檔
- **THEN** 指令在任何偵測開始之前以非 0 的結束碼結束並說明原因，不寫出結果資料夾
