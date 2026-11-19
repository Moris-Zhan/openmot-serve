## ADDED Requirements

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

## MODIFIED Requirements

### Requirement: Scoring follows the MOT17 pedestrian convention
計分 SHALL 遵循 MOTChallenge 的行人評測慣例：只評估行人類別，並依官方規則忽略被標為不列入評分的標註與干擾物。評測 MOT20 時，計分 SHALL 以 MOT20 的慣例執行，使 `non_mot_vehicle` 類別的標註與其他干擾物同樣被忽略；評測 MOT17 時維持 MOT17 的慣例，不因這個變更而改變。

#### Scenario: Ground truth marked as not considered
- **WHEN** 標註中含有被標為不列入評分的物件
- **THEN** 偵測到該物件不被算作誤報，沒偵測到也不被算作漏報

#### Scenario: MOT20 non-MOT vehicles are ignored
- **WHEN** 以 MOT20 計分，標註中含有 `non_mot_vehicle` 類別的物件，而追蹤結果在該物件上有一個框
- **THEN** 該框不被算作誤報；同樣的資料以 MOT17 計分時則被算作誤報

### Requirement: The score summary records how it was produced
分數摘要 SHALL 記錄偵測模型權重的識別資訊、推論後端、推論精度（例如 FP16）、所評測的 benchmark、最終追蹤參數、軟體版本與 GPU 型號，使不同次的結果可以被公平比較。略過調參而直接使用參數檔時，摘要 SHALL 註明參數來自參數檔且未調參，並記錄使用者給的參數檔路徑，使讀者不會把數字誤讀成針對該 benchmark 調過參。

#### Scenario: Summary metadata
- **WHEN** 一次 Benchmark Run 完成
- **THEN** 摘要檔含有上述全部中繼資料，且與該次實際使用的設定一致

#### Scenario: Parameters were not tuned for this benchmark
- **WHEN** 一次 Benchmark Run 以參數檔完成
- **THEN** 摘要記錄 benchmark、參數檔的路徑，並註明未調參
