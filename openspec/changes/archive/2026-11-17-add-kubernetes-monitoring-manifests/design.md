# Design

## Context

動機見 proposal.md。目前的事實：

- api 與 worker 已各自在獨立的埠提供指標（預設 `9101`、`9102`，容器內綁 `0.0.0.0`，由 `jobs/settings.py` 的 `DEFAULT_API_METRICS_PORT` 與 `DEFAULT_WORKER_METRICS_PORT` 提供預設值）。`deploy/k8s/base/` 的容器目前只宣告 api 的 `http:8000`，沒有指標埠；沒有 worker 的 Service。
- 原有的 `api` Service 型態是 ClusterIP，`overlays/k3s-single-node/` 以 patch 把它改成 NodePort（`30080`）。Kubernetes 會替 NodePort Service 上的**每一個**埠配置節點埠，所以指標埠不能放在這個 Service 上。
- 現有警報規則寫 `up{job="openmot-api"}` 與 `up{job="openmot-worker"}`（Compose 的 scrape job 名稱）。`ServiceMonitor` 預設把 `job` 設成 Service 名稱。
- `ServiceMonitor` 與 `PrometheusRule` 是 Prometheus Operator 的 CRD。這台開發環境沒有叢集、沒有 CRD schema 可以離線取得，也沒有 Helm；有 `kustomize` v5.5.0 與 `kubeconform` v0.6.7。
- worker 處理 Job 時會是 NotReady（Celery solo pool 無法回應 `worker.healthcheck` 的 ping，見 `docs/kubernetes.md` 的說明），但指標伺服器在獨立的執行緒，忙碌時仍可回應；api 在 Redis 不可用時 readiness 失敗而 NotReady。Service 預設只把 Ready 的 Pod 放進 endpoints。
- Compose 的 scrape 目標是寫死的，worker 停止時 `up` 變成 0；Kubernetes 的目標由服務發現產生，Pod 不存在或 Pending 時目標與 `up` 序列都不存在。
- 既有的 k8s 測試以 `tests/k8s_manifests.py` 的 `render()` 渲染 `deploy/k8s/` 下的目錄，並在 `kubeconform` 或 `kustomize` 不存在時略過。
- 實機驗證因沒有叢集而被阻擋，由 `verify-kubernetes-on-cluster` 第 4 節承接。

## Goals / Non-Goals

**Goals:**
- 在沒有叢集、沒有 Operator 的環境，用測試擋住會讓監控悄悄失效的錯誤：selector 對不上、埠名稱不存在、`job` 標籤與規則不一致、規則與規則檔分岐。
- 不改變既有行為：沒有安裝 Operator 的叢集仍能 `kubectl apply -k` 工作負載 overlay；對外的 `api` Service 不多出任何埠。

**Non-Goals:**
- 不安裝或打包 Prometheus Operator、Prometheus、Grafana，不提供 Helm chart 或 values。
- 不處理 Grafana dashboard 的載入方式（取決於使用者日後的 chart 設定）、Alertmanager、GPU 指標。
- 不宣稱 Prometheus 已經抓到資料；那是實機驗證的範圍。

## Decisions

**D1：指標用獨立的 `api-metrics` 與 `worker-metrics` Service，不加在既有的 `api` Service 上。**
NodePort 會替 Service 上每個埠配置節點埠，k3s overlay 的 patch 把 `api` 改成 NodePort，指標埠放上去就會公開到節點，違反 `observability` 規格「指標不經由對外的埠」。獨立的 ClusterIP Service 讓 overlay 的 NodePort patch 完全不受影響。替代方案：在 overlay 的 patch 裡移除指標埠——脆弱，base 與 overlay 的責任變得交錯，漏改就洩漏。
兩個 Service 放在 `base/`（核心資源，沒有 CRD 相依），使用與既有 Service 相同的 `app.kubernetes.io/name` 選取 Pod，並各自帶 `app.kubernetes.io/name: api-metrics`／`worker-metrics` 標籤讓 `ServiceMonitor` 選取（Service 的標籤與它選取的 Pod 標籤是兩回事）。

**D2：容器宣告具名的 `metrics` 埠；埠號由測試與程式的預設值比對。**
容器 `ports` 只是宣告，不影響程序實際監聽的埠，所以清單裡的號碼可能與程式預設悄悄不同。測試直接 import `jobs.settings` 的兩個預設值與清單比對，預設值改變而清單沒改時失敗。ConfigMap 不設 `OPENMOT_*_METRICS_PORT`（沿用預設），避免多一個要同步的來源。

**D3：用 `relabelings` 把 `job` 固定為 `openmot-api` 與 `openmot-worker`。**
`replace` 動作、不給 `sourceLabels`、只給 `targetLabel: job` 與 `replacement`。替代方案：改寫警報規則與 dashboard 去配合 Kubernetes 的預設 `job`（`api-metrics`）——Compose 與 Kubernetes 的規則就會分叉，規則的單元測試也要兩份。固定標籤讓同一份規則在兩邊通用。測試比對 `ServiceMonitor` 的 `replacement` 與規則檔 `ApiDown`、`WorkerDown` 所用的 `job` 值。

**D4：`PrometheusRule` 以副本形式放在清單中，並用測試保證與規則檔一致。**
kustomize 沒辦法把 Prometheus 的規則檔轉成 `PrometheusRule`（CRD 的 `spec.groups` 與規則檔的 `groups` 結構相同，但外面要包一層）。`monitoring/rules/openmot.rules.yml` 維持為唯一來源與單元測試（promtool）的對象，`deploy/k8s/monitoring/prometheusrule.yaml` 的 `spec.groups` 是副本，測試以 YAML 解析後比對兩者相等。替代方案：在建置時產生——多一個產生步驟與工具，這個規模不值得。

**D5：監控資源放在獨立的 `deploy/k8s/monitoring/`，不進 overlay。**
CRD 不存在的叢集上，`kubectl apply -k` 含 `ServiceMonitor` 的清單會失敗，把監控資源放進 overlay 會讓工作負載也跟著無法部署。獨立目錄由使用者在安裝 Operator 後單獨套用。目錄內不設 kustomize 的 `namespace:` 轉換器：`ServiceMonitor` 與 `PrometheusRule` 在 `monitoring` namespace，`monitoring` namespace 本身由 `namespace.yaml` 建立（Operator 的 chart 也可能建立，建立同名 namespace 是冪等的）。

**D6：`ServiceMonitor` 與 `PrometheusRule` 放在 `monitoring` namespace，以 `namespaceSelector.matchNames: [openmot]` 跨 namespace 選取，並帶 `release: monitoring` 標籤。**
`kube-prometheus-stack` 預設只選取帶有 `release: <chart 的 release 名稱>` 標籤的資源。`release: monitoring` 是假設的 release 名稱，文件要明確說明：若使用者的 release 名稱不同，要改標籤或調整 Prometheus 的 `serviceMonitorSelector`。這個假設無法在沒有叢集的情況下驗證，列入限制。

**D7：兩個指標 Service 設 `publishNotReadyAddresses: true`。**
worker 忙碌時 NotReady、api 在 Redis 不可用時 NotReady，預設的 Service 會在這些時候移除 endpoints，指標目標會在最需要看指標的時候消失。指標 Service 只給 Prometheus 用，不是流量入口，讓 NotReady 的 Pod 留在 endpoints 沒有導流的副作用。對外的 `api` Service **不**設這個欄位，否則流量會被導到 NotReady 的 Pod。限制：這個欄位保證 Service 的 endpoints 含有 NotReady 的 Pod；Prometheus 的服務發現是否抓取這些位址，要在實機驗證時確認（`verify-kubernetes-on-cluster` 4.2 在 worker 處理 Job 時檢查目標的 `up`）。

**D8：`ApiDown` 與 `WorkerDown` 納入「目標不存在」。**
條件改成 `up{job="…"} == 0 or absent(up{job="…"})`。`absent()` 在序列不存在時回傳一個沒有標籤（除了從等號比對推得的 `job`）的值，因此告警帶有的標籤只有 `job` 與規則自己的 `severity`；`for: 1m` 維持不變。Compose 的目標一直存在，`absent()` 為空，行為不變；沒有 worker 的 Compose 堆疊上 `WorkerDown` 觸發本來就是文件說明的預期行為。promtool 單元測試新增兩個案例（序列不存在持續超過 `for`、序列短暫不存在不觸發），並保留原有案例。替代方案：另開一條 `ApiTargetMissing` 規則——規則多一條、單元測試與文件多一份，而且兩條規則的語意（都是服務停擺）重複。
這會改動 Compose 也載入的規則檔，所以 `PrometheusRule` 副本與規則檔的等同性測試（D4）涵蓋這個改動。

**D9：`release: monitoring` 的覆寫方式只寫在文件，不做參數化。**
文件說明：release 名稱不同時，使用者建立自己的 kustomize overlay，引用 `deploy/k8s/monitoring`，並以 `labels`（`includeSelectors: false`）或 patch 覆寫 `release` 標籤。清單本身不加變數，因為 kustomize 沒有原生的參數替換，而這個假設無法在沒有叢集時驗證。

**D10：驗證分三層，各自只宣稱它證明的事。**
1. `kustomize build` 成功且 `kubeconform`（Kubernetes 1.31.0）通過核心資源（Service、Deployment、Namespace）；CRD 種類以 `-skip ServiceMonitor,PrometheusRule` 略過，因為沒有離線的 CRD schema。
2. 針對 CRD 資源的結構斷言（解析 YAML）：selector 對得上 Service 標籤、`endpoints.port` 是 Service 上存在的具名埠、`namespaceSelector` 指向 `openmot`、`job` 值與規則一致、`PrometheusRule` 與規則檔相同、工作負載 overlay 的渲染結果不含任何 CRD 種類。
3. 這些測試**不**證明 Prometheus 抓到資料；文件與 README 註記「未在叢集上驗證」，實機驗證在 `verify-kubernetes-on-cluster` 第 4 節。

## Risks / Trade-offs

- [標籤 `release: monitoring` 與使用者的 chart 不符，Operator 悄悄不選取資源] → 文件列出需要核對的 `serviceMonitorSelector` 與查詢指令（`kubectl get prometheus -o yaml`、Prometheus Targets 頁面），並列入未驗證的限制。
- [`PrometheusRule` 副本與規則檔分岐] → 等同性測試；規則檔改變而副本未改時失敗。
- [CRD 種類沒有 schema 檢查，欄位名稱打錯不會被擋] → 結構斷言涵蓋實際用到的欄位（`selector`、`namespaceSelector`、`endpoints[].port`、`path`、`interval`、`relabelings`、`spec.groups`）；其餘欄位的正確性留給實機驗證。
- [新增的兩個 Service 使既有測試中「Service 清單」的斷言失敗] → 在 base 階段更新這些斷言並確認原有的 `api` Service 斷言不變；這是預期的更新，不是行為退化。
- [api 與 worker 的 Pod 重建時 Service 的 endpoints 暫時為空，`up` 變 0] → 這正是 `ApiDown` 與 `WorkerDown` 設計要偵測的狀態（`for: 1m` 避開短暫重啟）；不需特別處理。

## Migration Plan

沒有資料或介面遷移。對既有叢集：套用新的 base（新增兩個 Service 與容器的具名埠，Pod 範本改變會讓 api 滾動更新、worker 重建；worker 的策略是 `Recreate`，會中斷正在處理的 Job，Celery 設定了 `acks_late`，Job 會重新投遞，但仍應在 worker 閒置時套用）；之後在已安裝 Operator 的叢集單獨套用 `deploy/k8s/monitoring/`。回復：刪除 `monitoring` 目錄的資源，並還原 base 的變更。

## Open Questions

- 實機上 `release: monitoring` 是否對得上使用者安裝的 chart：留給 `verify-kubernetes-on-cluster` 第 4 節確認，不影響這個 change 的範圍或任務。
