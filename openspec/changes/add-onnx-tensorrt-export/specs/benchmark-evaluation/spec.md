## ADDED Requirements

### Requirement: A Benchmark Run can select the inference backend
Benchmark Run 的指令 SHALL 接受推論後端與（可選的）產物路徑；未指定時使用 `torch` 後端與既有的預設行為。不支援的後端、不支援的後端與精度組合，或產物驗證失敗，SHALL 使指令以非 0 的結束碼結束並說明原因，且 SHALL 在任何偵測開始之前發生，不寫出結果資料夾。分數摘要 SHALL 記錄實際使用的後端；使用 ONNX 或 TensorRT 後端時，摘要 SHALL 另記錄所用產物的識別資訊（檔名與 sidecar 中的 ONNX sha256，TensorRT 另含函式庫版本與 GPU 型號）。

#### Scenario: Default backend is unchanged
- **WHEN** 使用者未指定後端而執行 Benchmark Run
- **THEN** 指令使用 `torch` 後端，分數與先前相同，摘要記錄後端為 `torch`

#### Scenario: Selecting an exported backend
- **WHEN** 使用者指定 `tensorrt` 與 `fp16` 並已建好對應的 engine
- **THEN** 指令使用該 engine 完成 Benchmark Run，摘要記錄後端為 `tensorrt` 與所用 engine 的識別資訊

#### Scenario: Unsupported combination is rejected early
- **WHEN** 使用者指定 `onnx` 搭配 `fp16`
- **THEN** 指令以非 0 的結束碼結束並說明 `onnx` 只支援 `fp32`，沒有載入模型、沒有偵測，也沒有寫出結果資料夾

#### Scenario: Artifact does not match
- **WHEN** 指定後端的產物未通過驗證（例如 sidecar 記錄的權重 sha256 與目前不同）
- **THEN** 指令以非 0 的結束碼結束並指名不符的項目，不寫出結果資料夾
