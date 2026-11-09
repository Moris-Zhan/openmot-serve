# Spec Delta

## Purpose

讓整套服務（api、worker、Redis）能用容器與 compose 在一台有 NVIDIA GPU 的機器上以一個指令啟動，並讓各容器靠共用儲存與健康檢查協同運作，作為後續 Kubernetes 部署的基礎。

## ADDED Requirements

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
映像 SHALL 不含權重檔、MOT17 資料與任何憑證；權重以唯讀掛載提供，並且 worker 在載入時驗證其 SHA-256。

#### Scenario: Inspect the image
- **WHEN** 檢查建置出的映像內容
- **THEN** 其中不含 `*.pth` 權重檔、`data/MOT17` 或憑證檔案
