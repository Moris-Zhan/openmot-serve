## MODIFIED Requirements

### Requirement: A TensorRT engine is built offline from the ONNX file
系統 SHALL 提供單一指令，由已匯出的 ONNX 檔建出 TensorRT engine，精度為 FP16（預設）或 INT8（校準規則見下一條需求），並將建置參數（精度旗標、workspace 大小、優化等級、TensorRT 版本、GPU 型號）寫入 sidecar JSON。engine SHALL NOT 被加入版本控制，且 SHALL 在服務啟動之外建置。指令在缺少 ONNX 檔、TensorRT 未安裝或建置失敗時 SHALL 以非 0 的結束碼結束並說明原因，不留下不完整的 engine 檔。

#### Scenario: Build succeeds
- **WHEN** 使用者在 ONNX 檔與 sidecar 齊全、TensorRT 可用的 GPU 機器上執行建置
- **THEN** 指令寫出 engine 與 sidecar，sidecar 含權重與 ONNX 的 sha256、輸入尺寸、精度、workspace、優化等級、TensorRT 版本與 GPU 型號，並以結束碼 0 結束

#### Scenario: Default precision is unchanged
- **WHEN** 使用者執行建置而沒有指定精度
- **THEN** 指令建出 FP16 engine，檔名與 sidecar 的內容與支援 INT8 之前相同

#### Scenario: ONNX file is missing
- **WHEN** 預期的 ONNX 檔不存在
- **THEN** 指令以非 0 的結束碼結束，並提示先執行 ONNX 匯出指令

#### Scenario: Build fails midway
- **WHEN** TensorRT 在建置過程中回報失敗
- **THEN** 指令以非 0 的結束碼結束，目標 engine 檔不存在

### Requirement: Backend and precision combinations are restricted
系統 SHALL 只接受下列後端與精度的組合：`torch` 搭配 `fp16` 或 `fp32`、`onnx` 搭配 `fp32`、`tensorrt` 搭配 `fp16` 或 `int8`。其他組合 SHALL 在載入模型之前被拒絕，錯誤訊息 SHALL 列出該後端支援的精度。不支援的後端名稱同樣 SHALL 被拒絕並列出支援的後端。

#### Scenario: Supported combination
- **WHEN** 使用者指定 `tensorrt` 與 `fp16`，或 `tensorrt` 與 `int8`
- **THEN** 設定被接受

#### Scenario: Unsupported combination
- **WHEN** 使用者指定 `onnx` 與 `fp16`
- **THEN** 設定被拒絕，錯誤訊息指出 `onnx` 只支援 `fp32`

#### Scenario: INT8 outside TensorRT
- **WHEN** 使用者指定 `torch` 或 `onnx` 搭配 `int8`
- **THEN** 設定被拒絕，錯誤訊息列出該後端支援的精度，沒有載入模型

#### Scenario: Unknown backend
- **WHEN** 使用者指定不存在的後端
- **THEN** 設定被拒絕，錯誤訊息列出支援的後端

## ADDED Requirements

### Requirement: An INT8 engine is calibrated on Tuning Half frames and the calibration is recorded
INT8 engine SHALL 以 TensorRT 的 entropy calibrator 做訓練後量化，建置旗標為 INT8 搭配 FP16 後備。校準影格 SHALL 只取自 MOT17 訓練序列（7 個 FRCNN 序列）的 Tuning Half，依固定規則均勻抽樣（預設 512 張），前處理與推論時相同；Validation Half 的影格 SHALL NOT 被使用。sidecar SHALL 記錄校準器、影格張數、挑選清單的 sha256 與校準快取的 sha256；相同的資料與參數 SHALL 得到相同的挑選清單。

#### Scenario: Frames come from the Tuning Half only
- **WHEN** 建置 INT8 engine 並挑選校準影格
- **THEN** 每一張都落在其序列的第 1 到 `floor(N/2)` 幀之內，沒有任何 Validation Half 的影格

#### Scenario: Selection is reproducible
- **WHEN** 以相同的資料與參數挑選兩次
- **THEN** 兩次的挑選清單與其 sha256 相同

#### Scenario: Calibration is recorded
- **WHEN** INT8 engine 建置成功
- **THEN** sidecar 含校準器名稱、影格張數、挑選清單的 sha256 與校準快取的 sha256，且精度為 `int8`

#### Scenario: Calibration data is missing
- **WHEN** 找不到 MOT17 訓練序列或可用的影格少於要求的張數
- **THEN** 指令以非 0 的結束碼結束並說明缺少什麼，沒有寫出 engine 或 sidecar

### Requirement: INT8 accuracy and speed are compared with FP16 and labeled by a fixed threshold
INT8 engine 在 Validation Half 完成完整的 Benchmark Run 後，文件 SHALL 記錄其合併 HOTA、MOTA、IDF1 與 PyTorch FP16 及 TensorRT FP16 的差，以及 INT8 與 FP16 的延遲與吞吐。合併 HOTA 與 PyTorch FP16 相差在 1.0 以內才 SHALL 標為「建議使用」，超過 SHALL 標為「不建議」。看過 Validation Half 的結果之後，SHALL NOT 為了通過門檻而調整校準設定。

#### Scenario: Within the threshold
- **WHEN** INT8 的合併 HOTA 與 PyTorch FP16 相差不超過 1.0
- **THEN** 文件把 INT8 標為「建議使用」並列出各項差距

#### Scenario: Beyond the threshold
- **WHEN** INT8 的合併 HOTA 與 PyTorch FP16 相差超過 1.0
- **THEN** 文件把 INT8 標為「不建議」並照實列出差距，變更仍算完成

#### Scenario: Speed is reported even when the gain is small
- **WHEN** INT8 的端到端延遲與 FP16 相差很小
- **THEN** 文件如實記錄純推論與端到端兩層的數字，不只報較好看的一層
