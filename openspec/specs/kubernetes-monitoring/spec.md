# kubernetes-monitoring Specification

## Purpose
讓 Kubernetes 上的 Prometheus 能發現並抓取 api 與 worker 的指標，並載入與 Compose 相同的警報規則：指標專用的 ClusterIP Service、`ServiceMonitor`、`PrometheusRule`，以及獨立於工作負載 overlay 的 `monitoring` 清單；並規範這些清單在沒有叢集時必須通過的檢查，以及必須如實標示的未驗證範圍。

## Requirements

### Requirement: Metrics are exposed through dedicated cluster-internal Services
系統 SHALL 為 api 與 worker 各提供一個只含指標埠的 ClusterIP Service（`api-metrics` 與 `worker-metrics`），指標埠以名稱 `metrics` 宣告，埠號與服務預設的指標埠一致（api `9101`、worker `9102`）。api 與 worker 的容器 SHALL 以同一個名稱宣告指標埠。兩個指標 Service SHALL 設定 `publishNotReadyAddresses: true`，使 worker 處理 Job 而無法回應健康檢查時、或 api 因 Redis 不可用而 NotReady 時，其指標目標仍然存在。原有的對外 `api` Service SHALL NOT 含有指標埠，也 SHALL NOT 設定 `publishNotReadyAddresses`，使 k3s overlay 把它改為 NodePort 時，指標不會被發佈到節點，流量也不會被導到 NotReady 的 Pod。

#### Scenario: Metrics Services are cluster-internal
- **WHEN** 渲染 k3s 單節點 overlay 並檢查 `api-metrics` 與 `worker-metrics`
- **THEN** 兩者的型態是 ClusterIP，各自只有一個名為 `metrics` 的埠，其 `targetPort` 指向容器上同名的埠

#### Scenario: Public Service does not carry metrics
- **WHEN** 渲染 k3s 單節點 overlay 並檢查型態為 NodePort 的 `api` Service
- **THEN** 它沒有任何指標埠，且渲染結果中沒有任何 NodePort 或 LoadBalancer 的 Service 含有指標埠

#### Scenario: NotReady Pods stay scrapeable
- **WHEN** 檢查渲染後的 `api-metrics` 與 `worker-metrics`，以及對外的 `api` Service
- **THEN** 兩個指標 Service 都設有 `publishNotReadyAddresses: true`，對外的 `api` Service 沒有設定

#### Scenario: Container port matches the service default
- **WHEN** 比對容器宣告的指標埠號與服務程式預設的指標埠號
- **THEN** api 與 worker 的埠號都相同，預設值改變而清單未跟著改變時檢查失敗

### Requirement: ServiceMonitors discover the metrics Services and keep the job names
系統 SHALL 提供兩個 `ServiceMonitor`，分別選取 `api-metrics` 與 `worker-metrics`，以 Service 上的具名埠 `metrics` 抓取 `/metrics`，並透過 relabeling 把 `job` 標籤固定為 `openmot-api` 與 `openmot-worker`，使現有警報規則與 dashboard 的查詢在 Kubernetes 與 Compose 上通用。`ServiceMonitor` 的 `namespaceSelector` SHALL 指向工作負載所在的 namespace，其 label selector SHALL 能選到對應 Service 的標籤。

#### Scenario: Selector matches the Service
- **WHEN** 渲染監控清單與工作負載 overlay，並以每個 `ServiceMonitor` 的 selector 與 `namespaceSelector` 比對 metrics Service
- **THEN** 每個 `ServiceMonitor` 恰好選到一個 Service，且該 Service 有 `endpoints.port` 所指名的埠

#### Scenario: Job label matches the alert rules
- **WHEN** 檢查 `ServiceMonitor` 設定的 `job` 值與警報規則中 `ApiDown`、`WorkerDown` 使用的 `job` 值
- **THEN** 兩者相同；任一方改變而另一方未同步時檢查失敗

### Requirement: Alert rules are shipped as a PrometheusRule identical to the rule file
系統 SHALL 提供一個 `PrometheusRule`，其規則群組與 `monitoring/rules/openmot.rules.yml` 完全相同，使 Compose 與 Kubernetes 共用同一組規則與同一份單元測試。

#### Scenario: Rules are identical
- **WHEN** 比對 `PrometheusRule` 的規則群組與規則檔的規則群組
- **THEN** 兩者相同；規則檔改變而 `PrometheusRule` 未跟著改變時檢查失敗

### Requirement: Monitoring resources are separate from the workload overlay
`ServiceMonitor`、`PrometheusRule` 與 `monitoring` namespace SHALL 放在獨立的 kustomize 目錄，由使用者單獨套用。工作負載的 overlay SHALL NOT 含有任何 Prometheus Operator 的自訂資源，使沒有安裝 Operator 的叢集仍可成功套用工作負載。`ServiceMonitor` 與 `PrometheusRule` SHALL 位於 `monitoring` namespace，並帶有讓常見的 Prometheus Operator 安裝能選到它們的標籤。

#### Scenario: Workload overlay needs no Operator CRDs
- **WHEN** 渲染 k3s 單節點 overlay
- **THEN** 結果不含 `ServiceMonitor`、`PrometheusRule` 或其他需要自訂資源定義的種類

#### Scenario: Monitoring render is valid without a cluster
- **WHEN** 在沒有叢集的環境渲染監控清單，並以 schema 檢查其中的核心資源
- **THEN** 渲染成功、核心資源沒有 schema 錯誤，且自訂資源通過結構斷言

### Requirement: Prerequisites and limits of the Kubernetes monitoring are documented
文件 SHALL 說明套用監控清單的前置條件（叢集已安裝 Prometheus Operator 與 Prometheus、`serviceMonitorSelector` 與 namespace selector 必須能選到這些資源）、清單帶有的 `release: monitoring` 標籤是假設的 release 名稱以及 release 名稱不同時如何以使用者自己的 overlay 覆寫，並如實標示限制：清單只經過渲染與結構測試，沒有在叢集上驗證 Prometheus 實際抓到資料；沒有 Kubernetes 的 Grafana dashboard 載入方式、沒有 Alertmanager；worker 指標需要有 GPU 的叢集才能端到端驗證。

#### Scenario: Prerequisites and limits are stated
- **WHEN** 讀取 Kubernetes 文件的監控一節
- **THEN** 文件寫出前置條件與上述限制，且沒有把未驗證的行為說成已驗證
