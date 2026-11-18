## ADDED Requirements

### Requirement: The result records GPU memory with one method for every backend
延遲量測的結果 SHALL 記錄 GPU 記憶體：偵測器載入之前、載入之後、量測結束之後各讀一次整張 GPU 的已用記憶體（單位 MiB），並記錄「載入之後」與「量測結束之後」各自相對於「載入之前」的增量。所有後端與精度 SHALL 用同一種量法，使數字可以直接比較。「載入之前」的讀數 SHALL 在量測程序使用 CUDA 之前取得，使增量包含該程序自己的 CUDA context。結果 SHALL 註明量的是整張 GPU 的已用量，含 CUDA context 與配置器的快取，而不是模型權重的大小；「載入之前」的已用量 SHALL 一併記錄，使 GPU 上有其他程序時可以從結果看出來。無法讀取 GPU 記憶體時（例如以 CPU 執行），量測 SHALL 照常完成，結果把記憶體欄位標為不可用，而不是失敗或寫出 0。

#### Scenario: Memory is recorded for a run
- **WHEN** 一次量測在 CUDA 裝置上完成
- **THEN** 結果檔含載入前、載入後與量測結束後的已用記憶體，以及兩個增量，且增量等於對應的讀數相減

#### Scenario: Same method for every backend
- **WHEN** 使用者以相同的參數分別對 `torch` 的 `fp16`、`tensorrt` 的 `fp16` 與 `tensorrt` 的 `int8` 執行量測
- **THEN** 三份結果都以相同的三個時間點與相同的單位記錄記憶體，使它們的增量可以直接比較

#### Scenario: A busy GPU is visible in the result
- **WHEN** 量測開始時 GPU 上已有其他程序占用記憶體
- **THEN** 結果的「載入之前」讀數反映該占用，使讀者能判斷增量是否被污染

#### Scenario: The human-readable table shows memory
- **WHEN** 一次量測完成
- **THEN** 人類可讀的表格含 GPU 記憶體的增量，且與結果檔的數字一致

#### Scenario: Memory cannot be read
- **WHEN** 量測在沒有 CUDA 的裝置上執行
- **THEN** 量測照常完成並寫出結果，結果的記憶體欄位標為不可用，表格不顯示假的數字
