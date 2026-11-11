# Spec Delta

## Purpose

讓整套服務（api、worker、Redis）能以宣告式清單部署到一台有 NVIDIA GPU 的單節點 k3s 叢集，由 Kubernetes 負責排程、健康判斷與重新拉起，並在 Pod 重建後保留 Job 狀態與 Result。

## ADDED Requirements

### Requirement: One kustomize apply deploys the whole service
系統 SHALL 提供一組 kustomize 清單，使單一 `kubectl apply -k` 即可在專屬 namespace 建立 api、worker、Redis 與它們所需的儲存與設定。除了事先放置好權重檔、讓映像可被叢集取得與安裝 GPU 裝置外掛之外，SHALL 不需要其他手動步驟。

#### Scenario: Cold deploy
- **WHEN** 在單節點 k3s 叢集（已有 GPU 裝置外掛、權重就位、映像可用）執行 `kubectl apply -k`
- **THEN** 三個工作負載都變成 Ready，經 Service 呼叫 `GET /health` 回應 `200`

#### Scenario: Manifests are valid before any cluster exists
- **WHEN** 在沒有叢集的環境以 kustomize 渲染並以 schema 檢查清單
- **THEN** 渲染成功且沒有 schema 錯誤

### Requirement: The worker is scheduled onto a GPU and never runs without one
worker 的 Pod SHALL 向叢集請求一張 NVIDIA GPU；叢集沒有可分配的 GPU 時，Pod SHALL 維持 Pending 並可由事件看出原因，而不是在沒有 GPU 的節點上執行。worker 容器內看不到 GPU 時 SHALL 啟動失敗，不接受 Job。

#### Scenario: GPU allocated
- **WHEN** 節點有可分配的 GPU
- **THEN** worker Pod 取得一張 GPU、通過環境檢查並開始接受 Job

#### Scenario: No GPU available
- **WHEN** 叢集中沒有可分配的 GPU
- **THEN** worker Pod 維持 Pending，且 api 與 Redis 不受影響

### Requirement: Job state and results survive Pod recreation
Job Status 與佇列 SHALL 存放在有持久儲存的 Redis；序列與 Result SHALL 存放在 api 與 worker 共用的持久儲存。刪除或重建任何一個 Pod SHALL 不遺失已完成 Job 的 Job Status 與 Result。

#### Scenario: Recreate api and worker
- **WHEN** 一個 Job 已 `succeeded` 後刪除 api 與 worker 的 Pod，等它們重新 Ready
- **THEN** 該 Job 的 Job Status 與 Result 仍可查詢與取回

#### Scenario: Recreate Redis
- **WHEN** 刪除 Redis 的 Pod 並等它重新 Ready
- **THEN** 先前 Job 的 Job Status 仍可查詢

### Requirement: An interrupted Job is completed after the worker Pod is replaced
worker Pod 在處理 Job 途中被刪除或異常結束時，Job SHALL 不永久停在 `running`；替代的 Pod Ready 後，該 Job SHALL 被重新處理並進入終態，且 Result 與未中斷時相同。

#### Scenario: Delete the worker mid-job
- **WHEN** Job 處於 `running` 時刪除 worker Pod
- **THEN** 替代的 worker Pod Ready 後，Job 最終進入 `succeeded`，Result 與 CLI 對同一序列的輸出逐位元組相同

### Requirement: Probes distinguish starting, unready and dead
每個工作負載 SHALL 宣告探針：載入偵測器期間的 worker SHALL 不會被判定為失敗而重啟；api 在無法接受 Job（佇列或儲存不可用）時 SHALL 從 Service 端點移除；正在處理長 Job 的 worker SHALL 不會因為暫時無法回應探針而被終止。

#### Scenario: Worker still loading the detector
- **WHEN** worker 剛啟動、尚未載入完偵測器
- **THEN** Pod 尚未 Ready，但不會被 kubelet 重啟

#### Scenario: Queue backend lost
- **WHEN** api 無法連到 Redis
- **THEN** api Pod 變成 NotReady 並從 Service 端點移除，`/health` 回應 `503`

#### Scenario: Worker busy with a long Job
- **WHEN** worker 正在處理一個耗時數分鐘的 Job
- **THEN** worker Pod 不會被 liveness 探針終止

### Requirement: Workloads declare resource bounds and a safe rollout
每個工作負載 SHALL 宣告 CPU 與記憶體的 requests 與 limits。worker SHALL 只有一個副本，並以先停舊再起新的方式更新，使新舊 worker 不會同時競爭同一張 GPU。收到終止訊號時，worker SHALL 有足以完成目前 Job 的優雅終止時間。

#### Scenario: Update the worker image
- **WHEN** 更新 worker 的映像並套用
- **THEN** 舊 worker Pod 先結束，新 worker Pod 才取得 GPU，期間不會有兩個 worker 同時存在

### Requirement: Pods run unprivileged and without embedded secrets
所有 Pod SHALL 以非 root 使用者執行且不要求特權；權重 SHALL 以唯讀方式掛入，不在映像或清單的內容中；清單 SHALL 不含任何憑證。

#### Scenario: Inspect the rendered manifests
- **WHEN** 檢查渲染後的清單
- **THEN** 沒有容器以 root 或 privileged 執行，權重掛載為唯讀，且找不到憑證類內容

### Requirement: Configuration comes from the manifests and invalid values fail visibly
儲存目錄、Redis 位址、權重路徑、精度、大小上限與逾時 SHALL 由清單中的設定提供，且與 compose 部署使用相同的環境變數名稱與預設值。無效的設定 SHALL 使對應的 Pod 啟動失敗並在日誌指出是哪個變數。

#### Scenario: Invalid precision
- **WHEN** 把精度設為不支援的值並套用
- **THEN** worker Pod 啟動失敗，日誌指出 `OPENMOT_PRECISION` 無效
