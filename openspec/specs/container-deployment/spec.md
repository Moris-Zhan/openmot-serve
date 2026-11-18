# container-deployment Specification

## Purpose
讓整套服務（api、worker、Redis）能用容器與 compose 在一台有 NVIDIA GPU 的機器上以一個指令啟動，並讓各容器靠共用儲存與健康檢查協同運作，作為後續 Kubernetes 部署的基礎。

## Requirements

### Requirement: One command starts the whole service
系統 SHALL 提供一個 compose 設定，使 `docker compose up` 即可啟動 api、worker 與 Redis，並讓它們使用同一個共用儲存。啟動後 SHALL 不需要額外的手動設定步驟，除了事先放置好權重檔。

#### Scenario: Cold start
- **WHEN** 在已安裝 NVIDIA Container Toolkit 且權重檔就位的機器上執行 `docker compose up`
- **THEN** 三個服務都啟動，`GET /health` 回應 `200`

#### Scenario: End to end through containers
- **WHEN** 服務啟動後，呼叫端提交一個影格 zip 並輪詢直到 `succeeded`
- **THEN** 取回的 Result 為 MOTChallenge 格式，且 worker 在 GPU 上完成偵測

### Requirement: The worker uses the GPU and refuses to run without it
worker 容器 SHALL 能使用主機的 NVIDIA GPU（目標為 RTX 5090、CUDA 12.8 以上）。偵測不到可用的 GPU 時，worker SHALL 啟動失敗並說明原因，而不是悄悄退回 CPU。

#### Scenario: GPU visible
- **WHEN** worker 容器啟動且 GPU 可見
- **THEN** 環境檢查通過，worker 開始接受 Job

#### Scenario: GPU missing
- **WHEN** 容器內看不到任何 GPU
- **THEN** worker 啟動失敗，輸出指出 CUDA 不可用，且不接受 Job

### Requirement: Containers share storage and keep state outside themselves
api 與 worker SHALL 透過同一個具名 volume 交換序列與 Result；Job Status 與佇列 SHALL 存在 Redis。重新建立 api 或 worker 容器 SHALL 不遺失已完成 Job 的 Result 與 Job Status。

#### Scenario: Recreate a container
- **WHEN** 一個 Job 已 `succeeded` 後重新建立 api 與 worker 容器
- **THEN** 該 Job 的 Job Status 與 Result 仍可查詢與取回

#### Scenario: Worker starts after api
- **WHEN** worker 比 api 晚啟動
- **THEN** 已排入佇列的 Job 在 worker 就緒後被處理，不會遺失

### Requirement: Containers are ordered by readiness and report their own health
compose SHALL 讓 api 與 worker 在 Redis 就緒後才啟動，並為每個服務提供健康檢查；不健康的服務 SHALL 在 `docker compose ps` 中可見。

#### Scenario: Redis not ready
- **WHEN** Redis 尚未就緒
- **THEN** api 與 worker 等待它就緒，而不是啟動後反覆失敗

#### Scenario: Unhealthy service
- **WHEN** 某個服務的健康檢查失敗
- **THEN** `docker compose ps` 將它標為 unhealthy

### Requirement: Images do not contain secrets, weights or data
映像 SHALL 不含權重檔、ONNX 檔、TensorRT engine、MOT17 資料與任何憑證；權重以唯讀掛載提供，並且 worker 在載入時驗證其 SHA-256；ONNX 檔與 engine 以掛載的目錄提供，並且 worker 在載入時驗證它們的 sidecar。

#### Scenario: Inspect the image
- **WHEN** 檢查建置出的映像內容（包含 TensorRT 的 worker 映像）
- **THEN** 其中不含 `*.pth` 權重檔、`*.onnx`、`*.engine`、`data/MOT17` 或憑證檔案

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

### Requirement: The TensorRT worker can serve an INT8 engine
TensorRT 的 compose override SHALL 讓 worker 的精度可由環境變數 `OPENMOT_PRECISION` 指定，未設定時為 `fp16`，且未設定時渲染出的設定 SHALL 與提供這個選項之前相同。以 `int8` 啟動並掛載對應的 INT8 engine 時，worker SHALL 通過產物的載入驗證、處理一個真實的 Job 到 `succeeded`，worker 的日誌與 Job 的中繼檔 SHALL 記錄後端為 `tensorrt`、精度為 `int8`。同一個 engine 以命令列處理同一個序列的 Result 與中繼檔 SHALL 與 worker 的逐位元組相同。精度與 engine 不符（例如以 `int8` 啟動卻掛載 FP16 engine，或未指定精度卻掛載 INT8 engine）時，worker SHALL 在處理任何 Job 之前結束並指名不符的項目。端對端驗證 SHALL 只使用專屬名稱的容器、卷與映像，唯讀使用權重卷，不動其他專案的資源。

#### Scenario: Default precision is unchanged
- **WHEN** 渲染疊加了 TensorRT override、且未設定 `OPENMOT_PRECISION` 的 compose 設定
- **THEN** worker 的環境變數為 `OPENMOT_BACKEND=tensorrt` 與 `OPENMOT_PRECISION=fp16`，與提供精度選項之前相同

#### Scenario: Precision is selected from the environment
- **WHEN** 渲染時設定 `OPENMOT_PRECISION=int8`
- **THEN** worker 的環境變數為 `OPENMOT_PRECISION=int8`，api 與 Redis 的設定不變

#### Scenario: An INT8 job succeeds
- **WHEN** worker 以 `int8` 與通過驗證的 INT8 engine 啟動，使用者提交一個影格序列
- **THEN** Job 進入 `succeeded`，Result 可取回，worker 日誌顯示 `backend=tensorrt precision=int8` 與 engine 路徑

#### Scenario: The served result equals the command line
- **WHEN** 以同一個 INT8 engine 在命令列處理同一個序列
- **THEN** worker 的 Result 與命令列的 Result 逐位元組相同，中繼檔也相同，且中繼檔記錄精度為 `int8`

#### Scenario: Precision and engine disagree
- **WHEN** worker 在未指定精度（預設 `fp16`）的情況下啟動，但掛載的是 INT8 engine
- **THEN** worker 在處理任何 Job 之前以非 0 的結束碼結束，錯誤訊息指名精度不符

#### Scenario: The verification does not touch other projects' resources
- **WHEN** 端對端驗證執行並結束
- **THEN** 只有專屬名稱（`openmot-trt-e2e` 開頭）的容器、卷與映像被建立與清除，`openmot` 專案的容器、卷與映像不變，權重卷只被唯讀掛載
