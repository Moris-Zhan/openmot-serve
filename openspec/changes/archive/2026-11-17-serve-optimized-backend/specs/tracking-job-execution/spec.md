## ADDED Requirements

### Requirement: The worker runs the detector backend chosen by configuration
系統 SHALL 允許以環境變數 `OPENMOT_BACKEND`（`torch`、`onnx` 或 `tensorrt`，預設 `torch`）與選用的 `OPENMOT_ENGINE`（ONNX 或 engine 檔的絕對路徑）選擇 worker 使用的偵測後端。兩者都未設定時，worker 的行為 SHALL 與沒有這個設定之前完全相同；`OPENMOT_ENGINE` 只能與 `onnx` 或 `tensorrt` 後端搭配，與 `torch` 後端同時設定時 worker SHALL 啟動失敗，避免使用者以為它生效。偵測器 SHALL 仍在 worker 啟動時載入一次，載入成功後 worker SHALL 在標準輸出記錄一行偵測器設定（後端、精度，以及有 engine 或 ONNX 檔時的產物路徑），且不輸出任何檔案內容。

#### Scenario: Default backend is unchanged
- **WHEN** worker 啟動時沒有設定 `OPENMOT_BACKEND` 與 `OPENMOT_ENGINE`
- **THEN** worker 使用 PyTorch 後端，與設定前的行為相同

#### Scenario: TensorRT backend is selected
- **WHEN** worker 以 `OPENMOT_BACKEND=tensorrt` 與 `OPENMOT_PRECISION=fp16` 啟動，且對應的 engine 存在並通過驗證
- **THEN** worker 載入該 engine 並接受 Job，每個 Job 以 TensorRT 偵測

#### Scenario: The chosen backend is visible in the log
- **WHEN** worker 以 `tensorrt` 後端與某個 engine 路徑啟動成功
- **THEN** 標準輸出有一行記錄後端、精度與該 engine 路徑；預設的 `torch` 後端記錄後端與精度，沒有產物路徑

#### Scenario: Engine path does not belong to the PyTorch backend
- **WHEN** `OPENMOT_ENGINE` 被設定，而後端是 `torch`（包含沒有設定 `OPENMOT_BACKEND` 的預設）
- **THEN** worker 啟動失敗，錯誤訊息指名 `OPENMOT_ENGINE` 與 `OPENMOT_BACKEND`，且不處理任何 Job

#### Scenario: Same output as the command line with the same backend
- **WHEN** 以相同的影格、權重、精度、追蹤參數與同一個後端（例如 `tensorrt`），分別透過服務與命令列的 `--backend` 處理
- **THEN** 兩者的 Result 與中繼檔內容完全相同

### Requirement: The backend configuration is validated at startup, before any Job
後端與 `OPENMOT_PRECISION` 的組合 SHALL 依 `inference-backends` 規定的對照驗證；不合法的組合與不存在的後端名稱 SHALL 在載入偵測器之前讓 worker 啟動失敗，錯誤訊息 SHALL 指名相關的環境變數，系統 SHALL NOT 由後端推斷精度。產物不存在、產物的 sidecar 驗證失敗（權重、GPU 型號、TensorRT 版本不符）、或所需的套件缺少時，worker SHALL 啟動失敗並說明原因，不處理任何 Job。

#### Scenario: Unsupported combination
- **WHEN** worker 以 `OPENMOT_BACKEND=onnx` 啟動而 `OPENMOT_PRECISION` 是預設的 `fp16`
- **THEN** worker 在載入偵測器之前啟動失敗，錯誤訊息指名 `OPENMOT_BACKEND` 與 `OPENMOT_PRECISION`，說明 `onnx` 只支援 `fp32`，且不處理任何 Job

#### Scenario: Unknown backend
- **WHEN** `OPENMOT_BACKEND` 是不存在的後端名稱
- **THEN** worker 啟動失敗，錯誤訊息指名 `OPENMOT_BACKEND` 並列出支援的後端

#### Scenario: Engine built for another GPU
- **WHEN** engine 的 sidecar 記錄的 GPU 型號與目前的 GPU 不同
- **THEN** worker 啟動失敗，錯誤訊息指名不符的項目並提示重新建置，且不處理任何 Job

#### Scenario: Backend package missing
- **WHEN** 選用 `tensorrt` 後端而映像裡沒有安裝 TensorRT
- **THEN** worker 啟動失敗，錯誤訊息指名缺少的套件與安裝方式，且不處理任何 Job
