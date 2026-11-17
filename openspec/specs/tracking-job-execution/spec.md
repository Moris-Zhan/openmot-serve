# tracking-job-execution Specification

## Purpose
讓排入佇列的 Tracking Job 被可靠地執行：用 v0.1 的追蹤管線處理 Sequence、把 Result 與 Job Status 如實記錄，並且在任何失敗下都讓 Job 進入可被查詢的終態，而不是卡在 `running` 或留下不完整的 Result。

## Requirements

### Requirement: Queued Jobs are processed with the existing tracking pipeline
系統 SHALL 從佇列取出 Tracking Job，以 `sequence-tracking` 相同的管線與預設追蹤參數處理其 Sequence，並把 Result 寫到共用儲存。同一份 Sequence 與相同設定所得的 Result SHALL 與命令列的結果逐位元組相同。

#### Scenario: Job runs to completion
- **WHEN** worker 取得一個 `queued` 的 Job
- **THEN** Job Status 先變為 `running`，處理完成後變為 `succeeded`，且 Result 已完整寫入

#### Scenario: Same output as the command line
- **WHEN** 以相同的影格、權重、精度與追蹤參數，分別透過服務與命令列處理
- **THEN** 兩者的 Result 內容完全相同

### Requirement: Every Job ends in a terminal state with a recorded reason
系統 SHALL 確保每個被取出的 Job 最終進入 `succeeded` 或 `failed`。處理過程中發生任何錯誤（影格損毀、偵測失敗、權重驗證失敗、儲存空間不足）時，Job Status SHALL 變為 `failed` 並記錄失敗原因，且不得留下不完整的 Result。

#### Scenario: Corrupt frame during processing
- **WHEN** Sequence 中有一個影格在處理時無法讀取
- **THEN** Job Status 變為 `failed`，記錄指出無法讀取的影格，且沒有 Result 檔

#### Scenario: Result is never partially visible
- **WHEN** 處理進行到一半
- **THEN** 共用儲存中不存在該 Job 的 Result 檔，直到完整寫入後才出現

#### Scenario: One failing Job does not stop the worker
- **WHEN** 一個 Job 失敗後佇列中還有其他 Job
- **THEN** worker 繼續處理後續的 Job

### Requirement: A Job is processed once
系統 SHALL 不讓同一個 Job 被兩個 worker 同時處理，也 SHALL 不在 worker 當機後讓 Job 永遠停在 `running`。

#### Scenario: Worker crashes mid-job
- **WHEN** worker 在處理 Job 的途中被終止
- **THEN** 該 Job 不會停留在 `running`：它被重新排入並完成，或被標為 `failed` 並註明原因

#### Scenario: Duplicate delivery
- **WHEN** 同一個 Job 被投遞兩次
- **THEN** 只有一次實際處理，Result 內容不受影響

### Requirement: The detector is loaded once per worker
系統 SHALL 在 worker 啟動時載入偵測器並驗證權重完整性，之後處理的每個 Job 重用它；權重驗證失敗時 worker SHALL 拒絕處理 Job 並回報原因。

#### Scenario: Weights fail verification
- **WHEN** 權重檔的 SHA-256 與預期不符
- **THEN** worker 啟動失敗並指出原因，不處理任何 Job

#### Scenario: Consecutive Jobs
- **WHEN** worker 連續處理兩個 Job
- **THEN** 第二個 Job 不再重新載入權重

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
