# kubernetes-deployment Specification

## Purpose
以宣告式清單描述整套服務（api、worker、Redis），讓它能部署到一台有 NVIDIA GPU 的單節點 k3s 叢集，由 Kubernetes 負責排程、健康判斷與重新拉起，並把 Job 狀態與 Result 放在持久儲存上。本規格只涵蓋可在沒有叢集的環境檢查的清單屬性；需要叢集才能觀察的行為另見後續變更 `verify-kubernetes-on-cluster`。

## Requirements

### Requirement: One kustomize render describes the whole service
系統 SHALL 提供一組 kustomize 清單，使單一 `kubectl apply -k` 即可在專屬 namespace 建立 api、worker、Redis 與它們所需的儲存與設定。清單 SHALL 在沒有叢集的環境也能渲染並通過 schema 檢查。

#### Scenario: Manifests are valid before any cluster exists
- **WHEN** 在沒有叢集的環境以 kustomize 渲染並以 schema 檢查清單
- **THEN** 渲染成功且沒有 schema 錯誤

#### Scenario: One render contains every workload
- **WHEN** 渲染 k3s 單節點 overlay
- **THEN** 結果包含 api、worker、Redis 三個工作負載、共用的儲存、設定，以及專屬的 namespace，且所有資源都在該 namespace 內

### Requirement: The worker requests one GPU through the NVIDIA runtime
worker 的 Pod SHALL 向叢集請求一張 NVIDIA GPU 並指定 NVIDIA runtime，使它只會被排程到有可分配 GPU 的節點。

#### Scenario: Rendered worker requests a GPU
- **WHEN** 檢查渲染後的 worker
- **THEN** 它的資源限制包含 `nvidia.com/gpu: 1` 且 `runtimeClassName` 為 `nvidia`

### Requirement: Durable storage is declared for state and results
Job Status 與佇列 SHALL 存放在以持久儲存宣告的 Redis；序列與 Result SHALL 存放在 api 與 worker 共用的持久儲存。

#### Scenario: Redis persists through an append-only file
- **WHEN** 檢查渲染後的 Redis
- **THEN** 它啟用 append-only file，且資料目錄掛載自它自己的持久儲存宣告

#### Scenario: api and worker share one claim
- **WHEN** 檢查渲染後的 api 與 worker
- **THEN** 兩者都在同一個路徑掛載同一個持久儲存宣告

### Requirement: Probes are declared according to whether a restart helps
每個工作負載 SHALL 宣告探針，且探針的種類 SHALL 反映「重啟是否有幫助」：api 的 liveness SHALL 只檢查連接埠而 readiness SHALL 檢查 `/health`；worker SHALL 宣告 startup 與 readiness 探針且 SHALL NOT 宣告 liveness 探針。

#### Scenario: api probes
- **WHEN** 檢查渲染後的 api
- **THEN** readiness 為 `httpGet /health`、liveness 只做連接埠檢查、startup 不依賴 Redis

#### Scenario: worker probes
- **WHEN** 檢查渲染後的 worker
- **THEN** startup 與 readiness 執行 worker 的健康檢查指令、沒有 liveness 探針，且 startup 的總寬限時間足以涵蓋載入偵測器

### Requirement: Workloads declare resource bounds and a safe rollout
每個工作負載 SHALL 宣告 CPU 與記憶體的 requests 與記憶體 limits。worker SHALL 只有一個副本，並以先停舊再起新的策略更新；它的優雅終止時間 SHALL 大於單一 Job 的時間上限。

#### Scenario: Rendered worker rollout
- **WHEN** 檢查渲染後的 worker
- **THEN** 副本數為 1、策略為 `Recreate`，且優雅終止時間大於設定中的任務時間上限

#### Scenario: Resource bounds everywhere
- **WHEN** 檢查渲染後的每個容器
- **THEN** 都宣告 CPU 與記憶體的 requests 與記憶體 limits

### Requirement: Pods run unprivileged and without embedded secrets
所有 Pod SHALL 以非 root 使用者執行且不要求特權；權重 SHALL 以唯讀方式掛入，不在映像或清單的內容中；清單 SHALL 不含任何憑證。

#### Scenario: Inspect the rendered manifests
- **WHEN** 檢查渲染後的清單
- **THEN** 沒有容器以 root 或 privileged 執行，權重掛載為唯讀，且找不到憑證類內容

### Requirement: Configuration names and defaults match the compose deployment
儲存目錄、Redis 位址、權重路徑、精度、大小上限與逾時 SHALL 由清單中產生的設定提供，且與 compose 部署使用相同的環境變數名稱與預設值。

#### Scenario: Config matches compose
- **WHEN** 渲染清單並與 compose 的環境變數比對
- **THEN** compose 設定的每個變數都存在於產生的設定中且值相同，並且載入後等於程式的預設值
