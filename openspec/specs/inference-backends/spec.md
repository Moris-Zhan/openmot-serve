# inference-backends Specification

## Purpose
把偵測網路匯出成 ONNX 並建成 TensorRT engine，讓 PyTorch、ONNX Runtime 與 TensorRT 三個推論後端共用同一套前處理與後處理、只在推論引擎上不同；並規範產物的命名、載入前的驗證與後端對 PyTorch 的驗收，使延遲與準確度的差異只能歸因於推論引擎。

## Requirements

### Requirement: The detection network can be exported to ONNX with one command
系統 SHALL 提供單一指令，載入已驗證的 YOLOX-X 權重，匯出只含網路本體（不含 letterbox、人物篩選與 NMS）、輸入固定為 `1×3×800×1440`、FP32 的 ONNX 檔，並以 ONNX 的結構檢查驗證匯出結果。匯出的檔案 SHALL 與一份記錄來源的 sidecar JSON 一起寫出。目標檔案已存在時，指令 SHALL 拒絕覆蓋。

#### Scenario: Export succeeds
- **WHEN** 使用者在權重存在且完整的情況下執行匯出
- **THEN** 指令寫出 ONNX 檔與 sidecar，sidecar 含權重 sha256、ONNX sha256、輸入尺寸、opset 與匯出所用的 torch 版本，並以結束碼 0 結束

#### Scenario: Weights are corrupted
- **WHEN** 權重檔的 sha256 與已知值不符
- **THEN** 指令以非 0 的結束碼結束並說明原因，不寫出任何產物

#### Scenario: Output already exists
- **WHEN** 目標 ONNX 檔已存在
- **THEN** 指令以非 0 的結束碼結束，不覆蓋既有檔案

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

### Requirement: Artifacts are named after what they were built from
ONNX 檔與 engine 檔的檔名 SHALL 由模型名稱、權重 sha256 的前 8 個字元與輸入尺寸組成；engine 檔名另 SHALL 含 TensorRT 版本、GPU 型號與精度。產物預設 SHALL 位於被版本控制忽略的 `models/engines/`。

#### Scenario: Different weights
- **WHEN** 兩份權重的 sha256 前 8 個字元不同
- **THEN** 它們對應的產物檔名不同，不會互相覆蓋

#### Scenario: Different GPU or TensorRT version
- **WHEN** 同一份權重在不同的 GPU 型號或 TensorRT 版本上建置 engine
- **THEN** engine 檔名不同

### Requirement: An artifact is verified before it is used
後端載入 ONNX 檔或 engine 前 SHALL 讀取 sidecar 並逐項比對：權重 sha256、ONNX sha256、輸入尺寸，engine 另比對 TensorRT 版本與 GPU 型號。任何一項不符，載入 SHALL 失敗並指名不符的項目；產物或 sidecar 不存在時，錯誤訊息 SHALL 含預期的檔名與對應的建置指令。使用者 SHALL 可用明確的路徑覆寫預設位置，覆寫的路徑同樣要通過驗證。

#### Scenario: Everything matches
- **WHEN** sidecar 記錄的各項與目前的權重、檔案、輸入尺寸、函式庫與 GPU 一致
- **THEN** 後端載入成功

#### Scenario: Weights changed after the build
- **WHEN** 目前的權重 sha256 與 sidecar 記錄的不同
- **THEN** 載入失敗，錯誤訊息指名權重 sha256 不符

#### Scenario: Engine built on another GPU or TensorRT version
- **WHEN** sidecar 記錄的 GPU 型號或 TensorRT 版本與目前環境不同
- **THEN** 載入失敗，錯誤訊息指名是 GPU 型號或 TensorRT 版本不符，並提示重新建置

#### Scenario: Artifact is missing
- **WHEN** 預期的產物不存在
- **THEN** 載入失敗，錯誤訊息含預期的檔名與建置指令

#### Scenario: Explicit path
- **WHEN** 使用者以明確路徑指定產物
- **THEN** 後端使用該路徑，並對它做同樣的 sidecar 驗證

### Requirement: Backends differ from the PyTorch backend only in the inference engine
ONNX 與 TensorRT 後端 SHALL 重用 PyTorch 後端的前處理（letterbox 與資料搬運）與後處理（人物篩選、NMS 與還原座標），只替換推論階段；推論階段 SHALL 接受與 PyTorch 後端相同的輸入張量，並回傳形狀與意義相同的 `23625×85` 輸出。後端 SHALL 直接使用 GPU 上的張量作為輸入與輸出，使推論階段不含額外的主機與裝置之間的拷貝。

#### Scenario: Same output contract
- **WHEN** 任一後端對同一張影格完成偵測
- **THEN** 回傳的陣列與 PyTorch 後端有相同的欄位（左、上、右、下、分數）、座標系與型別

#### Scenario: No host round trip during inference
- **WHEN** 後端執行推論階段
- **THEN** 輸入張量與輸出張量都留在 GPU 上，推論階段不呼叫主機端拷貝

### Requirement: ONNX Runtime runs only on the CUDA execution provider
ONNX 後端 SHALL 只使用 CUDA 執行提供者。CUDA 執行提供者無法載入時，載入 SHALL 失敗並說明原因，SHALL NOT 悄悄退回 CPU。

#### Scenario: CUDA provider unavailable
- **WHEN** onnxruntime 無法載入 CUDA 執行提供者（例如安裝了需要不同 CUDA 版本的建置）
- **THEN** 載入失敗並說明原因，不以 CPU 推論繼續

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

### Requirement: Backends are checked against PyTorch on real frames
ONNX 後端（FP32）在 30 張 MOT17 真實影格上的偵測結果，SHALL 與 PyTorch FP32 後端相比：偵測數量相同、各框座標差不超過 0.5 像素、各分數差不超過 1e-3。TensorRT 後端（FP16）與 ONNX 後端 SHALL 各自在 Validation Half 上跑完整的 Benchmark Run，其 HOTA SHALL 與 PyTorch FP16 的 HOTA 相差不超過 0.5。驗證結果 SHALL 記錄在文件中。

#### Scenario: ONNX detections match PyTorch
- **WHEN** 同一批 30 張真實影格分別以 ONNX 與 PyTorch FP32 偵測
- **THEN** 每張的偵測數量相同，且座標與分數的差都在上述容差內

#### Scenario: TensorRT HOTA stays close
- **WHEN** TensorRT FP16 完成 Validation Half 的 Benchmark Run
- **THEN** 其合併 HOTA 與 PyTorch FP16 的合併 HOTA 相差不超過 0.5

### Requirement: Heavy dependencies are optional
ONNX 與 TensorRT 所需的套件（`onnx`、`onnxruntime-gpu`、`tensorrt-cu12`）SHALL 只放在獨立的 `[export]` extra 並固定版本。預設安裝、預設測試套件與預設的持續整合 SHALL NOT 需要它們；需要它們的測試 SHALL 在套件缺少時被略過而非失敗。使用者選用的後端所需套件缺少時，SHALL 得到指出缺少哪個套件與如何安裝的錯誤訊息。

#### Scenario: Default environment
- **WHEN** 在未安裝 `[export]` extra 的環境執行預設測試
- **THEN** 測試全部通過或被略過，不因缺少套件而失敗

#### Scenario: Backend selected without its package
- **WHEN** 使用者選用 `tensorrt` 後端但環境沒有安裝 TensorRT
- **THEN** 指令以非 0 的結束碼結束，錯誤訊息指名缺少的套件並提示安裝 `[export]` extra

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
