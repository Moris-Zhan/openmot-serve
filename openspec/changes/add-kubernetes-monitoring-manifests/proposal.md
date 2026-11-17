## Why

v0.4 的監控（Prometheus 指標、Grafana dashboard、警報規則）目前只能用 Docker Compose 的 overlay 啟動；在 Kubernetes 上，api 與 worker 的指標埠沒有任何 Service，Prometheus Operator 也沒有 `ServiceMonitor` 可以發現它們，所以 v0.3 的清單部署到叢集後完全看不到指標。路線圖 v0.4 的最後一項「Kubernetes monitoring manifests」就是補上這一段。

現有清單還有四個會讓監控悄悄失效的地方：

- 現有警報規則寫 `job="openmot-api"` 與 `job="openmot-worker"`，而 `ServiceMonitor` 預設把 `job` 標籤設成 Service 名稱，`ApiDown` 與 `WorkerDown` 在 Kubernetes 上永遠不會觸發。
- k3s overlay 把 api Service 改成 NodePort，如果把指標埠加在同一個 Service 上，指標會被一起發佈到節點，違反「指標不經由對外的埠」的既有規格。
- worker 用 Celery solo pool，處理 Job 時無法回應健康檢查而變成 NotReady（api 在 Redis 不可用時也會 NotReady）；Service 預設只把 Ready 的 Pod 放進 endpoints，指標目標會在最需要看指標的時候消失。
- 在 Kubernetes 上 Pod 不存在或卡在 Pending（例如沒有 GPU）時，scrape 目標根本不存在，`up` 序列也不存在，`up == 0` 永遠不成立，`ApiDown` 與 `WorkerDown` 對最常見的故障靜默；Compose 的目標是寫死的，所以沒有這個問題。

## What Changes

- 在 `deploy/k8s/base/` 為 api 與 worker 的容器加上具名的 `metrics` 埠（預設 `9101`、`9102`，與程式的預設值一致），並新增兩個只含指標埠的 ClusterIP Service：`api-metrics` 與 `worker-metrics`，兩者設 `publishNotReadyAddresses: true`，讓 NotReady 的 Pod 仍被抓取。原有的 `api` Service 不加指標埠，因此 NodePort 不會發佈指標。
- 新增 `deploy/k8s/monitoring/`，自成一個 kustomize：`monitoring` namespace、兩個 `ServiceMonitor`（跨 namespace 選取 `openmot` 的 metrics Service，用 `relabelings` 把 `job` 固定為 `openmot-api` 與 `openmot-worker`）、一個 `PrometheusRule`（內容與 `monitoring/rules/openmot.rules.yml` 相同）。
- 把 `ApiDown` 與 `WorkerDown` 的條件改成「目標 `up` 為 0，**或目標不存在**」（`up{job="…"} == 0 or absent(up{job="…"})`），並同步更新規則檔、promtool 單元測試與 `PrometheusRule` 副本。Compose 的行為不變（目標一直存在）。
- 這組資源**不放進**現有的 overlay：它們依賴 Prometheus Operator 的 CRD，放進去會讓沒有 Operator 的叢集 `kubectl apply -k` 失敗。
- 測試：渲染、核心資源的 schema 檢查，以及針對 CRD 資源的結構斷言（selector 對得上 Service 標籤、`endpoints.port` 是 Service 上存在的具名埠、`job` 值與規則一致、`PrometheusRule` 與規則檔的 `groups` 相同）。
- 文件：`docs/kubernetes.md` 說明前置條件（叢集需已安裝 Prometheus Operator 與 Prometheus、`serviceMonitorSelector` 要對上 `release: monitoring`）；`docs/observability.md` 與 README 路線圖如實標示「只有清單與結構測試，未在叢集上驗證」。
- 不包含：Grafana dashboard 的 Kubernetes 載入方式、Alertmanager、Helm chart、在叢集上的實機驗證（由 `verify-kubernetes-on-cluster` 第 4 節承接）、GPU 指標（dcgm-exporter）。

## Capabilities

### New Capabilities
- `kubernetes-monitoring`: Kubernetes 上 api 與 worker 指標的發現與警報規則——指標專用的 Service、`ServiceMonitor`、`PrometheusRule`、獨立的 `monitoring` 清單，以及它們必須通過的檢查與必須標示的限制。

### Modified Capabilities
- `observability`: 「Alert rules are verified by unit tests」中服務停擺的條件納入「目標不存在」；「Observability is documented with its limits」中「Kubernetes 的監控清單不在範圍內」的限制改為「已有清單與結構測試，未在叢集上驗證」。

## Impact

- 程式碼：沒有 Python 執行路徑的變更。
- 規則：`monitoring/rules/openmot.rules.yml` 與 `openmot.rules.test.yml`（`ApiDown`、`WorkerDown` 納入目標不存在的情況）；Compose 的 Prometheus 載入同一份規則，行為不變。
- 清單：`deploy/k8s/base/api.yaml`、`worker.yaml`、`kustomization.yaml`（新增兩個 Service 與具名埠）；新增 `deploy/k8s/monitoring/`。
- 測試：新增監控清單的測試；既有的 base 與 overlay 測試若斷言了確切的資源清單，需要隨新增的 Service 更新。
- 文件：`docs/kubernetes.md`、`docs/observability.md`、`docs/kubernetes-status.md`、README 路線圖。
- 風險：Prometheus 實際是否抓到資料取決於叢集裡的 Operator 與 selector 設定，這個 change 不驗證。
