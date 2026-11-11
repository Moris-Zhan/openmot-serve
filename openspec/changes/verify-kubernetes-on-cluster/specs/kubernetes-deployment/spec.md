# Spec Delta

## ADDED Requirements

### Requirement: A cold deploy reaches Ready
在已備妥 GPU 裝置外掛、權重與映像的單節點 k3s 叢集上，單一 `kubectl apply -k` SHALL 使 api、worker 與 Redis 都變成 Ready。除了事先放置權重、讓映像可被叢集取得與安裝 GPU 裝置外掛之外，SHALL 不需要其他手動步驟。

#### Scenario: Cold deploy
- **WHEN** 在單節點 k3s 叢集（已有 GPU 裝置外掛、權重就位、映像可用）執行 `kubectl apply -k`
- **THEN** 三個工作負載都變成 Ready，經 Service 呼叫 `GET /health` 回應 `200`

### Requirement: The worker waits for a GPU and never runs without one
叢集沒有可分配的 GPU 時，worker 的 Pod SHALL 維持 Pending 並可由事件看出原因，而不是在沒有 GPU 的節點上執行。worker 容器內看不到 GPU 時 SHALL 啟動失敗，不接受 Job。

#### Scenario: GPU allocated
- **WHEN** 節點有可分配的 GPU
- **THEN** worker Pod 取得一張 GPU、通過環境檢查並開始接受 Job

#### Scenario: No GPU available
- **WHEN** 叢集中沒有可分配的 GPU
- **THEN** worker Pod 維持 Pending，事件指出缺少 `nvidia.com/gpu`，且 api 與 Redis 不受影響

### Requirement: Job state and results survive Pod recreation
刪除或重建任何一個 Pod SHALL 不遺失已完成 Job 的 Job Status 與 Result。

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

### Requirement: Probes behave correctly at runtime
探針 SHALL 在實際執行時區分啟動中、未就緒與已死亡：載入偵測器期間的 worker SHALL 不會被判定為失敗而重啟；api 在無法接受 Job 時 SHALL 從 Service 端點移除；正在處理長 Job 的 worker SHALL 不會因為暫時無法回應探針而被終止。

#### Scenario: Worker still loading the detector
- **WHEN** worker 剛啟動、尚未載入完偵測器
- **THEN** Pod 尚未 Ready，但不會被 kubelet 重啟

#### Scenario: Queue backend lost
- **WHEN** api 無法連到 Redis
- **THEN** api Pod 變成 NotReady 並從 Service 端點移除，`/health` 回應 `503`，且 api 不被重啟

#### Scenario: Worker busy with a long Job
- **WHEN** worker 正在處理一個耗時數分鐘的 Job
- **THEN** worker Pod 不會被終止

### Requirement: Updating the worker never runs two workers at once
更新 worker 時，新舊 worker SHALL 不會同時存在而競爭同一張 GPU；收到終止訊號時，worker SHALL 有足以完成目前 Job 的優雅終止時間。

#### Scenario: Update the worker image
- **WHEN** 更新 worker 的映像並套用
- **THEN** 舊 worker Pod 先結束，新 worker Pod 才取得 GPU，期間不會有兩個 worker 同時存在

### Requirement: Invalid configuration fails the Pod visibly
無效的設定值 SHALL 使對應的 Pod 啟動失敗並在日誌指出是哪個變數。

#### Scenario: Invalid precision
- **WHEN** 把精度設為不支援的值並套用
- **THEN** worker Pod 啟動失敗，日誌指出 `OPENMOT_PRECISION` 無效
