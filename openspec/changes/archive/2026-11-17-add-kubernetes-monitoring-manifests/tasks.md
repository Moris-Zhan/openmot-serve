# Tasks

## 1. 指標專用的 Service 與具名埠（kubernetes-monitoring）

- [x] 1.1 先寫失敗的測試再修改 `deploy/k8s/base/`：api 與 worker 的容器各宣告名為 `metrics` 的埠（`9101`、`9102`），新增 `api-metrics` 與 `worker-metrics` 兩個 ClusterIP Service（各自只有一個名為 `metrics` 的埠，`targetPort` 指向同名的容器埠，帶 `app.kubernetes.io/name: api-metrics`／`worker-metrics` 標籤、設 `publishNotReadyAddresses: true`），並列入 `base/kustomization.yaml`；測試直接 import `jobs.settings` 的 `DEFAULT_API_METRICS_PORT` 與 `DEFAULT_WORKER_METRICS_PORT` 與清單比對；驗證：新測試通過，且把清單的埠號改錯、把 Service 型態改成 NodePort、把指標埠加到 `api` Service、移除指標 Service 的 `publishNotReadyAddresses`、在 `api` Service 加上 `publishNotReadyAddresses` 這五個突變都會讓測試失敗
- [x] 1.2 更新既有 base 與 overlay 測試中斷言確切 Service 清單的部分，並新增 overlay 測試：渲染 k3s 單節點 overlay 後，NodePort 的 `api` Service 沒有指標埠，且沒有任何 NodePort 或 LoadBalancer 的 Service 含有指標埠；驗證：`pytest tests/test_k8s_base_*.py tests/test_k8s_overlay_k3s.py` 全部通過，原有的 `api` Service 斷言沒有被放寬

## 2. 監控清單與它的檢查（kubernetes-monitoring）

- [x] 2.1 先寫失敗的測試再新增 `deploy/k8s/monitoring/`：`namespace.yaml`（`monitoring`）、`servicemonitor-api.yaml` 與 `servicemonitor-worker.yaml`（位於 `monitoring` namespace、帶 `release: monitoring` 標籤、`namespaceSelector.matchNames: [openmot]`、以 `matchLabels` 選取對應的 metrics Service、`endpoints` 用具名埠 `metrics`、`path: /metrics`、`interval: 15s`、`relabelings` 把 `job` 固定為 `openmot-api` 與 `openmot-worker`）、`kustomization.yaml`；驗證：渲染測試通過，且目錄內沒有 kustomize 的 `namespace:` 轉換器
- [x] 2.2 先寫失敗的 promtool 測試再修改 `monitoring/rules/openmot.rules.yml`：`ApiDown` 與 `WorkerDown` 的條件改成 `up{job="…"} == 0 or absent(up{job="…"})`；`openmot.rules.test.yml` 新增案例（序列不存在持續超過 `for` 時觸發、序列短暫不存在時不觸發），並保留原有案例；驗證：`promtool test rules` 通過，且把 `absent(...)` 拿掉或把 `for` 改短的突變會讓新案例失敗，Compose 的既有端到端測試（`tests/test_monitoring_e2e.py`、`tests/test_alert_rules.py`）不修改且通過
- [x] 2.3 先寫失敗的測試再新增 `deploy/k8s/monitoring/prometheusrule.yaml`：位於 `monitoring` namespace、帶 `release: monitoring` 標籤，`spec.groups` 是 `monitoring/rules/openmot.rules.yml` 的副本；驗證：等同性測試通過，且在規則檔改一條 `expr` 而不改副本時測試失敗
- [x] 2.4 新增監控清單的結構測試：每個 `ServiceMonitor` 的 selector 與 `namespaceSelector` 恰好選到渲染後工作負載 overlay 中一個 metrics Service、`endpoints.port` 是該 Service 上存在的埠名、`job` 的 `replacement` 等於規則檔 `ApiDown` 與 `WorkerDown` 的 `job`；並新增測試確認渲染 k3s 單節點 overlay 後不含 `ServiceMonitor`、`PrometheusRule` 或其他 CRD 種類；驗證：測試通過，且改錯 selector 標籤、改錯埠名、改錯 `job` 值三個突變都會讓測試失敗
- [x] 2.5 新增監控清單的 schema 測試：`kustomize build deploy/k8s/monitoring` 成功，並以 `kubeconform`（Kubernetes 1.31.0、`-skip ServiceMonitor,PrometheusRule`）檢查核心資源，工具不存在時略過；驗證：測試通過，並確認 `-skip` 之外的核心資源（例如 Namespace）確實被檢查

## 3. 文件與路線圖（kubernetes-monitoring、observability）

- [x] 3.1 在 `docs/kubernetes.md` 新增「監控」一節（繁體中文）：前置條件（叢集已安裝 Prometheus Operator 與 Prometheus）、`kubectl apply -k deploy/k8s/monitoring` 的指令、`release: monitoring` 標籤的假設、release 名稱不同時如何用使用者自己的 kustomize overlay 以 `labels` 或 patch 覆寫、如何核對 `serviceMonitorSelector`（`kubectl get prometheus -A -o yaml`）、Prometheus Targets 的檢查方法，並標示限制（只經渲染與結構測試、未在叢集上驗證、沒有 Grafana dashboard 的 Kubernetes 載入方式、沒有 Alertmanager、worker 指標需要 GPU 才能端到端驗證）；驗證：文件測試檢查章節與四項限制存在，文件中的指令與目錄名稱和 repo 一致
- [x] 3.2 更新 `docs/observability.md`：警報規則表中 `ApiDown`、`WorkerDown` 的條件改為「目標 `up == 0` 或目標不存在」，並說明 Kubernetes 上 Pod 不存在或 Pending 時由 `absent()` 偵測；「驗證與限制」把「Kubernetes 的監控清單不在範圍內」改成「已有清單與結構測試，未在叢集上驗證」；更新 `docs/kubernetes-status.md` 的盤點、對照表與相關 change 一節；驗證：既有的 observability 文件測試通過，且文件沒有把未驗證的行為說成已驗證
- [x] 3.3 更新 README 路線圖 v0.4：勾選「Kubernetes monitoring manifests」並註明「manifests, render and structure tests only; not verified on a live cluster」；驗證：`pytest tests/test_readme_k8s.py` 通過，必要時依新狀態更新該測試，且 v0.3 的兩個未勾選項目維持未勾選

## 4. 整體檢查（integration）

- [x] 4.1 執行預設的完整測試、`ruff check .` 與 `openspec validate add-kubernetes-monitoring-manifests --strict`、`openspec validate --specs --strict`；驗證：全部通過，且沒有新增被略過的測試是因為工具不存在以外的原因

## Workflow follow-up

- 完成後用 `/opsx:archive` 封存這個變更並把 delta spec 同步到主規格；`kubernetes-monitoring` 是新的主規格，delta 沒有 Purpose，封存時需補上一段 Purpose。
- 叢集上的實機驗證由 `verify-kubernetes-on-cluster` 第 4 節承接。
