# Proposal

## Why

v0.1 已經有一條可重現、可量化的追蹤管線，但它只能在命令列對本機資料夾執行。專案的目標是「把多目標追蹤做成生產服務」：呼叫端要能提交一個 Sequence、不必等到追蹤結束、之後再取回 Result。追蹤一個 Sequence 要數十秒到數分鐘，同步 HTTP 請求不適合，所以需要非同步的 Tracking Job，並且要能用容器一鍵啟動，後續的 Kubernetes（v0.3）與觀測（v0.4）才有可部署的單元。

## What Changes

- 新增 `app/`：FastAPI 服務，提供 `POST /jobs`、`GET /jobs/{job_id}`、`GET /jobs/{job_id}/result`、`GET /health`。提交時接受一個影格 zip，驗證後存到共用儲存並回傳 `202 Accepted` 與 `job_id`。
- 新增 `worker/`：Celery worker 從 Redis 取出 Tracking Job，呼叫 v0.1 的 `tracking` 管線，把 MOTChallenge 格式的 Result 寫到共用儲存，並更新 Job Status。偵測器只在 worker 啟動時載入一次。
- Job Status 存在 Redis（`queued`、`running`、`succeeded`、`failed`），序列與結果存在共用 volume，不自動清理。
- 新增 `deploy/docker/`：api、worker（CUDA 12.8 基底，經 NVIDIA Container Toolkit 使用 RTX 5090）與 Redis 的 Dockerfile 與 docker-compose，一個指令啟動整套服務。
- 失敗要可見：無法解壓、沒有影格、影格損毀、偵測失敗都要讓 Job 進入 `failed` 並帶有原因，且不留下不完整的 Result。
- 不包含：影片檔輸入與解碼、Job 保留期限與自動清理、驗證與授權、Kubernetes、Prometheus 的 `/metrics`、CI、CPU 版 worker、INT8 與 ONNX／TensorRT。這些屬於後續階段或不在 v0.2 範圍。

## Capabilities

### New Capabilities
- `tracking-job-api`: 以 HTTP 提交 Sequence、查詢 Job Status、取回 Result，並驗證輸入。
- `tracking-job-execution`: 從佇列取出 Tracking Job，用既有管線處理，並可靠地記錄狀態、Result 與失敗原因。
- `container-deployment`: 以容器與 compose 啟動 api、worker 與 Redis，並透過共用儲存與健康檢查協同運作。

### Modified Capabilities

（無。既有的 `sequence-tracking` 行為不變，worker 只是呼叫它。）

## Impact

- **新增程式碼**：`app/`、`worker/`、`deploy/docker/`，以及對應的 `tests/`；`tracking/` 與 `eval/` 不修改。
- **新增依賴**：FastAPI、uvicorn、python-multipart、Celery、redis client；容器端需要 Docker、Docker Compose 與 NVIDIA Container Toolkit。
- **外部系統**：Redis（官方映像）。
- **文件**：新增服務與部署說明；README 路線圖 v0.2 勾選。
- **風險**：上傳 zip 是不受信任的輸入，需防止路徑穿越（zip slip）與壓縮炸彈，並限制大小。GPU 容器只能在有 NVIDIA 驅動的機器上端到端驗證，因此預設測試要在沒有 GPU 與 Redis 的情況下也能執行，真實的整合驗證另以標記區分。
