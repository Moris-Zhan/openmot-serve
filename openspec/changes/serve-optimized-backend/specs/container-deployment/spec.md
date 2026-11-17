## MODIFIED Requirements

### Requirement: Images do not contain secrets, weights or data
映像 SHALL 不含權重檔、ONNX 檔、TensorRT engine、MOT17 資料與任何憑證；權重以唯讀掛載提供，並且 worker 在載入時驗證其 SHA-256；ONNX 檔與 engine 以掛載的目錄提供，並且 worker 在載入時驗證它們的 sidecar。

#### Scenario: Inspect the image
- **WHEN** 檢查建置出的映像內容（包含 TensorRT 的 worker 映像）
- **THEN** 其中不含 `*.pth` 權重檔、`*.onnx`、`*.engine`、`data/MOT17` 或憑證檔案

## ADDED Requirements

### Requirement: A TensorRT worker image is a separate build target and leaves the default image unchanged
系統 SHALL 在 worker 的 Dockerfile 提供名為 `tensorrt` 的 build target，內含固定版本的 ONNX 與 TensorRT 相關套件與匯出、建置 engine 所需的程式；預設 target 產生的映像的執行行為 SHALL 與提供這個 target 之前相同，且不含 ONNX Runtime 與 TensorRT 這兩個重依賴。系統 SHALL 提供 compose override，以該 target 建置 worker、設定 `OPENMOT_BACKEND=tensorrt` 與 `OPENMOT_PRECISION=fp16`，並以唯讀掛載 engine 所在的目錄。engine 與 ONNX 檔 SHALL 以這個映像內的匯出與建置指令離線產生到掛載的目錄，使 TensorRT 版本與執行時相同。映像 SHALL 只含一個空的 `/models/engines` 目錄且屬於非 root 的執行使用者，使新建的具名卷第一次掛載時繼承擁有者，建置與執行都不需要 root。

#### Scenario: Engine directory is empty and writable by the runtime user
- **WHEN** 檢查 worker 映像中的 `/models/engines`
- **THEN** 它是空的目錄，擁有者是非 root 的執行使用者，且映像其他位置沒有 `*.engine` 或 `*.onnx`

#### Scenario: Default image is unchanged
- **WHEN** 以預設 target 建置 worker 映像並檢查其中安裝的套件
- **THEN** 沒有安裝 ONNX Runtime 與 TensorRT

#### Scenario: TensorRT image has the pinned packages
- **WHEN** 以 `tensorrt` target 建置 worker 映像並檢查其中安裝的套件
- **THEN** 安裝了固定版本的 ONNX、ONNX Runtime 與 TensorRT，且可執行匯出與建置 engine 的指令

#### Scenario: Compose override selects the TensorRT worker
- **WHEN** 渲染疊加了 TensorRT override 的 compose 設定
- **THEN** worker 以 `tensorrt` target 建置，環境變數為 `OPENMOT_BACKEND=tensorrt` 與 `OPENMOT_PRECISION=fp16`，engine 目錄以唯讀掛載，且 api 與 Redis 的設定不變

#### Scenario: Engine is built by the image that runs it
- **WHEN** 使用者依文件以 TensorRT 映像建置 engine 並啟動 worker
- **THEN** engine 的 sidecar 記錄的 TensorRT 版本與 GPU 型號和執行中的環境相同，worker 啟動成功
