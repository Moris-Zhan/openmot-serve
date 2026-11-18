# v0.3 + v0.4 — 單機 k3s Pod／GPU／故障恢復／監控整合測試計畫

> 版本：v1.2｜日期：2026-11-18｜狀態：v0.3 待執行；v0.4 的 monitoring 清單已完成（第 11 節），叢集實機驗證仍待執行
>
> 適用範圍：單台 Linux 主機、單節點 k3s、API／Worker／Redis／PVC、GPU Scheduling、Pod Recovery，以及 v0.4 Prometheus／Grafana／ServiceMonitor。
>
> 本文件是**測試設計與驗收計畫**，不是已完成的測試報告。以下 namespace、映像、overlay 與工作負載名稱均為範例，執行前應依專案實際 manifests 調整。第 11 節的 v0.4 工作分成「清單已撰寫並通過 render／結構測試」與「叢集實機驗證」兩種狀態，前者已完成，後者尚未做。

## 1. 目標與驗收邊界

對應 v0.3 尚未勾選需求：

- [ ] **GPU scheduling for the worker**：`nvidia.com/gpu` 已宣告，但尚需確認 GPU Device Plugin、k3s NVIDIA Runtime、排程與容器內實際運算。
- [ ] **Live k3s verification**：在真實單節點 k3s 上驗證 Deployment、GPU Scheduling、Pod Recovery，記錄於 `docs/kubernetes.md`。

### 核心限制

1. **單機可以測 Pod 重建與容器重啟，不能證明跨節點容錯**；若整台主機關機，沒有其他 Node 可以接手服務。
2. **沒有實體 NVIDIA GPU 時**，可以完成非 GPU 路徑、Pod 恢復和「GPU 資源不足時 Pending」的負向測試，**不能宣稱 GPU Scheduling / CUDA 運算通過**。
3. GPU Operator **不在本次範圍**；可使用 NVIDIA Device Plugin，但需確實部署與驗證。GPU Extended Resource 必須在 `limits` 宣告；若同時填 `requests`，數值需相等。
4. Pod 的 `Running` 只代表程序已啟動，還需驗證 **Ready、連線、工作執行、GPU 運算與資料保存**。
5. k3s 預設 local-path PVC 為單節點本機儲存；**Pod 重建資料保存**不等於**主機或磁碟故障資料不遺失**。

## 2. 目標架構

```mermaid
flowchart TB
  HOST[Linux 單機主機<br/>CPU / RAM / NVIDIA GPU（選配）]
  subgraph K3S[Single-node k3s]
    API[API Deployment / Service]
    WORKER[Worker Deployment<br/>GPU limit: 1 或 CPU-only overlay]
    REDIS[Redis Deployment / Service]
    PVC[(PVC / local-path)]
    PLUGIN[NVIDIA Device Plugin<br/>GPU 測試階段才需要]
    API --> REDIS
    WORKER --> REDIS
    REDIS --> PVC
    PLUGIN -. 提供 nvidia.com/gpu .-> WORKER
  end
  HOST --> K3S
```

建議 namespace：`openmot-test`（可自行替換）；優先使用既有 Kustomize base + k3s overlay，不另建一套與正式配置脫節的 manifests。

## 3. 環境方案選擇

| 方案 | 用途 | 優點 | 風險／限制 | 建議 |
|---|---|---|---|---|
| A. **Linux 宿主機原生 k3s** | 完整驗收 | GPU/runtime/volume 網路路徑較直接 | 需要宿主機管理權限，可能影響既有服務 | **首選** |
| B. Docker 特權容器內 k3s | 快速做非 GPU 測試 | 沿用既有環境 | nested runtime、inotify、cgroup、GPU passthrough 複雜 | 僅做補充 |
| C. 無 GPU 單機 | CPU-only 回歸 | 可先驗證業務邏輯、恢復與 PVC | GPU 成功排程驗收無法完成 | 可先做 Phase 0–2、4 的非 GPU 部分 |

### 目前已知阻塞事項

先前 Docker 內 k3s 發生 `failed to create cni conf monitor ... too many open files`，且觀測到 `fs.inotify.max_user_instances=128`、Node 未註冊。**必須先解除此阻塞，或改為宿主機原生 k3s**。不要在 Node 尚未 Ready 時宣稱後續 Pod 測試有效。調整 `sysctl` 前需確認具管理權限與正確宿主機／核心。

## 4. 測試計畫表

| ID | 階段 | 工作項目 | 具體操作／檢查 | 完成條件 | 優先級 | 預估工時* | 狀態 |
|---|---|---|---|---|---|---|---|
| T00 | Phase 0 | 主機資源盤點 | 核心、OS、CPU/RAM/磁碟、`nvidia-smi`、容器執行環境 | 產出環境紀錄 | P0 | 0.5h | ☐ |
| T01 | Phase 0 | 解決 inotify / k3s 啟動 | `sysctl`、`journalctl`、k3s 日誌，確認正確宿主機 | k3s 正常啟動，無 CNI/inotify 阻斷 | P0 | 1–3h | ☐ |
| T02 | Phase 0 | Node / DNS / Storage 基線 | `kubectl get nodes -o wide`、`get pods -A`、`get storageclass` | 1 Node Ready、核心系統 Pod 正常 | P0 | 0.5h | ☐ |
| T03 | Phase 1 | Manifest 靜態檢查 | `kubectl kustomize <overlay>`；API schema / dry-run 檢查 | YAML 可 render 且 schema 驗證通過 | P0 | 0.5h | ☐ |
| T04 | Phase 1 | 建置測試 namespace | `kubectl create namespace openmot-test` | namespace 存在 | P0 | 0.25h | ☐ |
| T05 | Phase 1 | 套用 CPU-only Deployment | 避免無 GPU 時 GPU Worker 長期 Pending；部署 API、CPU Worker、Redis | 所有非 GPU Pod Ready | P0 | 1h | ☐ |
| T06 | Phase 1 | Service / PVC 檢查 | `get svc,pvc,pv -n openmot-test` | Service 存在，PVC Bound | P0 | 0.5h | ☐ |
| T07 | Phase 2 | API → Redis 連線 | API 健康檢查、Redis ping、基本寫讀 | 請求成功，Redis 寫讀一致 | P0 | 0.5h | ☐ |
| T08 | Phase 2 | Worker → Redis 任務 | enqueue → Worker consume → result | 單筆端到端工作通過 | P0 | 1h | ☐ |
| T09 | Phase 2 | CPU/RAM / probes | 檢視 request/limit、startup/readiness/liveness | 定義可見，探針結果符合預期 | P1 | 0.5h | ☐ |
| T10 | Phase 3 | GPU 宿主機前置檢查 | `nvidia-smi`、NVIDIA container runtime | Driver、runtime 正常 | P1 | 0.5–1h | ☐ |
| T11 | Phase 3 | Device Plugin 安裝與檢查 | 依官方相容說明安裝，`kubectl describe node` | `Allocatable nvidia.com/gpu >= 1` | P1 | 1h | ☐ |
| T12 | Phase 3 | RuntimeClass 與 CUDA smoke test | 確認 `runtimeClassName: nvidia` 或正確預設 runtime；跑 CUDA Pod | GPU Pod Ready/Completed，CUDA 運算成功 | P1 | 1h | ☐ |
| T13 | Phase 3 | 真實 Worker GPU 工作 | 啟用 GPU overlay／限制 GPU=1，送小型推論任務 | Worker 不 Pending，推論成功，GPU 被使用 | P1 | 1h | ☐ |
| T14 | Phase 3 | GPU 資源不足負向測試 | 單 GPU 被占用時建立第二個 GPU Pod | 第二 Pod Pending 且事件指向資源不足 | P2 | 0.5h | ☐ |
| T15 | Phase 4 | Deployment Pod 重建 | `kubectl delete pod`，觀察 ReplicaSet 補建 | 新 Pod Ready，功能恢復 | P1 | 0.5h | ☐ |
| T16 | Phase 4 | Liveness / Readiness 行為 | 故意讓健康端點失敗 | liveness 促成重啟／readiness 摘除流量 | P1 | 1h | ☐ |
| T17 | Phase 4 | Redis PVC 持久化 | 寫入測試資料、按實際 Redis persistence 設定落盤、重建 Pod | 資料成功恢復（前提 AOF/RDB 已啟用） | P1 | 1h | ☐ |
| T18 | Phase 4 | Worker 中斷與任務重試 | 執行中 kill Worker，驗證 queue ack/retry | 無無聲遺失，重試/去重符合設計 | P1 | 1h | ☐ |
| T19 | Phase 5 | 收集觀測證據 | node/pods/events/describe/logs/PVC/結果/時間 | 每個案例有輸出與 PASS/FAIL | P0 | 0.5h | ☐ |
| T20 | Phase 5 | 更新 Runbook 與驗收結論 | `docs/kubernetes.md`、問題單、版本記錄 | 明確標示已驗證與未驗證範圍 | P0 | 0.5h | ☐ |

\* 工時是規劃估計，非保證時程；環境故障、GPU 驅動相容性及應用程式準備可能使時間增加。

## 5. 建議執行命令（逐階段）

### Phase 0：主機與 k3s 基線

```bash
uname -a
cat /etc/os-release
free -h
df -h
sysctl fs.inotify.max_user_instances fs.inotify.max_user_watches
command -v nvidia-smi && nvidia-smi || echo 'GPU not available or driver absent'

# 原生 k3s 主機（須有對應權限）
sudo systemctl status k3s --no-pager
sudo journalctl -u k3s -n 100 --no-pager
sudo k3s kubectl get nodes -o wide
sudo k3s kubectl get pods -A
sudo k3s kubectl get storageclass
```

**Gate 0：** Node 必須 `Ready`，CoreDNS/必要系統 Pod 正常。若未通過，停止往下測試。

### Phase 1：部署 CPU-only 測試組

先準備 CPU-only Worker overlay：**移除 GPU request/limit 與 NVIDIA RuntimeClass**，但保留 Worker 其他實際部署參數。不要透過模擬 GPU 資源冒充正式 GPU 驗收。

```bash
# <CPU_OVERLAY> 請替換為現有專案 overlay 實際路徑
kubectl kustomize <CPU_OVERLAY> > /tmp/openmot-cpu-rendered.yaml
kubectl create namespace openmot-test --dry-run=client -o yaml | kubectl apply -f -

# 只有在 manifest namespace 設定和 openmot-test 相容時才執行
kubectl apply --dry-run=server -f /tmp/openmot-cpu-rendered.yaml
kubectl apply -f /tmp/openmot-cpu-rendered.yaml
kubectl get deployments,pods,svc,pvc -n openmot-test -o wide
kubectl get events -n openmot-test --sort-by=.lastTimestamp
```

部署前檢查 manifest 中 namespace、imagePullPolicy、imagePullSecrets、StorageClass、Service port、探針與實際環境一致。如使用本機自建映像，先匯入 k3s containerd 或配置可信任 Registry，避免 `ImagePullBackOff`。

**Gate 1：** API／CPU Worker／Redis 的副本均 Ready、PVC Bound，無持續 CrashLoopBackOff。

### Phase 2：Pod 通訊與工作流

```bash
kubectl get svc,endpointslices -n openmot-test
kubectl logs -n openmot-test deployment/<api-deployment> --tail=100
kubectl logs -n openmot-test deployment/<worker-deployment> --tail=100
kubectl logs -n openmot-test deployment/<redis-deployment> --tail=100
kubectl port-forward -n openmot-test service/<api-service> 18080:<service-port>
# 另開終端機：curl -f http://127.0.0.1:18080/<health-path>
```

API / Redis / Worker 的 endpoint、queue key、payload 必須採用**實際專案協定**；建議至少投入 1 個有唯一 `job_id` 的工作並比對輸出結果。若服務端口或名稱不符，以真實 manifest 為準。

**Gate 2：** API 連線成功、Worker 能消費任務並回傳正確狀態。

### Phase 3：GPU Scheduling（有 NVIDIA GPU 才執行成功路徑）

```bash
nvidia-smi
command -v nvidia-container-runtime
kubectl get runtimeclass
kubectl describe node <node-name> | grep -A12 -E 'Capacity:|Allocatable:'
kubectl get pods -A -o wide | grep -i nvidia || true
```

k3s 文件指出需安裝 NVIDIA Container Runtime 並讓 k3s 正確偵測；未設定 NVIDIA 為預設 runtime 時，Pod 通常要指定 `runtimeClassName: nvidia`。GPU Device Plugin 需與既有驅動及 k3s containerd 相容。**只宣告 `nvidia.com/gpu: 1` 不代表 GPU 已可用。**

GPU smoke test（須先安裝並確認 RuntimeClass、Device Plugin 及映像可取得）：

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: gpu-smoke
  namespace: openmot-test
spec:
  restartPolicy: Never
  runtimeClassName: nvidia  # 若已設正確 NVIDIA 預設 runtime，可依環境調整
  containers:
    - name: cuda
      image: nvcr.io/nvidia/k8s/cuda-sample:nbody
      args: ["nbody", "-gpu", "-benchmark"]
      resources:
        limits:
          nvidia.com/gpu: 1
```

```bash
kubectl apply -f gpu-smoke.yaml
kubectl get pod gpu-smoke -n openmot-test -w
kubectl logs -n openmot-test gpu-smoke
kubectl describe pod -n openmot-test gpu-smoke
```

GPU smoke 通過後再測**真正的 GPU Worker Deployment**，並以實際模型的小型推論確認 GPU 使用；否則僅可勾選 smoke test，不可勾選 Worker GPU 功能。若只有一張 GPU，第二個請求 1 GPU 的 Pod 應在已占用時 Pending；驗證後刪除測試 Pod 釋放 GPU。

**Gate 3：** `nvidia.com/gpu` 出現在 Node allocatable，CUDA Pod 成功，GPU Worker 成功跑完任務。

### Phase 4：Pod 恢復與 PVC

```bash
# 先核對 Deployment 的 replicas >= 1、工作負載名稱
kubectl get deploy -n openmot-test
kubectl delete pod -n openmot-test <worker-pod-name>
kubectl get pods -n openmot-test -w
kubectl rollout status -n openmot-test deployment/<worker-deployment> --timeout=180s
kubectl get events -n openmot-test --sort-by=.lastTimestamp

# 儲存驗證
kubectl get pvc,pv -n openmot-test
kubectl describe pvc -n openmot-test <redis-pvc-name>
```

- **Pod Delete**：確認 ReplicaSet 補建新 Pod；若 Pod 含長任務，檢查是否可安全重試。
- **Liveness**：在受控測試環境使 liveness 失敗，檢查 `restartCount` 與恢復時間；不得將手動刪 Pod 當作 liveness 驗收。
- **Readiness**：使 readiness 失敗並確認 Service 不導流至未 Ready Pod；僅一個 API replica 時服務可能短暫不可用。
- **Redis Persistence**：要先確定 `appendonly yes`（AOF）或 RDB 規則、檔案目錄與 PVC mount 正確，再寫入辨識用 key、觸發持久化、重建、檢查 key 是否仍在；否則「PVC Bound」不能代表 Redis 資料會保存。
- **Worker 任務安全**：測試 at-least-once queue 重試與冪等處理（若系統有實作）；若沒有，將其標為功能缺口，不能單靠 Pod 補建判定任務恢復。
- **不包括**：整台單機斷電後的高可用、跨節點排程、跨節點 PVC 搬移、GPU 硬體故障切換。

**Gate 4：** Pod 可補建，probe 行為符合定義，Redis 資料與任務安全按實際設計驗證。

## 6. 最小驗收案例矩陣

| Case | 情境 | 前置條件 | 預期結果 | 證據 | 結果 |
|---|---|---|---|---|---|
| TC-01 | k3s 單機啟動 | k3s 安裝 | Node Ready | `get nodes` | ☐ PASS / ☐ FAIL |
| TC-02 | API/Redis/CPU Worker 啟動 | CPU overlay | Deployment Ready | `get pods,deploy` | ☐ PASS / ☐ FAIL |
| TC-03 | Service / Job E2E | 容器 Ready | 任務成功完成 | API result / logs | ☐ PASS / ☐ FAIL |
| TC-04 | PVC 綁定 | StorageClass 正常 | PVC Bound | `get pvc` | ☐ PASS / ☐ FAIL |
| TC-05 | GPU Node 資源發現 | GPU+Driver+Plugin | `nvidia.com/gpu >= 1` | `describe node` | ☐ PASS / ☐ FAIL / ☐ N/A |
| TC-06 | CUDA smoke test | Runtime + Plugin | GPU 程式成功 | GPU Pod logs | ☐ PASS / ☐ FAIL / ☐ N/A |
| TC-07 | Worker 實際 GPU 推論 | GPU Worker image | 推論成功 | worker logs/result | ☐ PASS / ☐ FAIL / ☐ N/A |
| TC-08 | 第二 GPU Pod 資源不足 | 1 GPU 已被占 | Pending + insufficient GPU event | `describe pod` | ☐ PASS / ☐ FAIL / ☐ N/A |
| TC-09 | 刪除 Worker Pod | Deployment 管理 | 自動補建並 Ready | events/pods | ☐ PASS / ☐ FAIL |
| TC-10 | Liveness / Readiness 失敗 | Probes 已設定 | 依探針種類重啟或撤流 | events/endpoints | ☐ PASS / ☐ FAIL |
| TC-11 | Redis Pod 重建 | PVC + AOF/RDB | 持久化資料還在 | key check/logs | ☐ PASS / ☐ FAIL |
| TC-12 | Worker 中斷後任務一致性 | 有佇列重試策略 | 重試/去重符合規格 | job IDs / logs | ☐ PASS / ☐ FAIL / ☐ N/A |

## 7. 測試結果紀錄模板

```markdown
### TC-XX：<測試名稱>
- 測試日期：
- Host OS / Kernel：
- k3s 版本：
- NVIDIA Driver / GPU / Runtime / Device Plugin（若適用）：
- Manifest commit / image digest：
- 測試步驟：
- 實際結果：
- 預期結果：
- 證據位置（logs / events / screenshots）：
- 結論：PASS / FAIL / BLOCKED / N/A
- 問題單與重測日期：
```

建議記錄 Pod 名稱、Node 名稱、時間戳、`kubectl describe` 中關鍵 Events、CrashLoop / Restart 次數及 GPU 使用證據。**BLOCKED 不能視為 PASS。**

## 8. 完成標準與勾選規則

### 可以勾選「GPU scheduling for the worker」的條件

- [ ] TC-05 GPU 資源可辨識。
- [ ] TC-06 實際 GPU CUDA Pod 成功。
- [ ] TC-07 **真實 Worker** GPU 工作完成。
- [ ] GPU Operator 未部署的假設／Device Plugin 來源與版本有記錄。

### 可以勾選「Verify deployment, GPU scheduling and pod recovery on a live k3s cluster」的條件

- [ ] TC-01–04：叢集與主要服務部署通過。
- [ ] TC-05–07：GPU 成功路徑通過。
- [ ] TC-09–11：Deployment 補建、Probes、Redis 持久化通過。
- [ ] TC-12：若業務流程要求任務不中斷或重試，完成相應驗證。
- [ ] `docs/kubernetes.md` 已包含環境、指令、驗收紀錄與單機限制。

> 若**沒有 GPU**：可以宣告「Single-node CPU Deployment / Pod Recovery verified」，但應保持兩項包含 GPU 的完整 v0.3 需求**未勾選**，直到 GPU 實機驗證完成。

## 9. 風險、回復與環境清理

| 風險 | 預防措施 |
|---|---|
| inotify / cgroup / CNI 無法啟動 | 原生 k3s 優先；先取證再修改宿主機 sysctl；保留現有 Docker 容器與資料 |
| GPU Pod Pending | 檢查 Device Plugin、allocatable、RuntimeClass、已占用 GPU 及 Events |
| GPU Pod 起來卻不能 CUDA | 檢查 driver/runtime/映像 CUDA 相容性與實際 workload |
| Registry 拉取失敗 | 先驗證本機 containerd image 或 registry 憑證與網路 |
| Redis 重建遺失資料 | 先開啟持久化並使用獨立測試資料，避免直接破壞正式資料 |
| 單機資源不足 | 先限定 CPU/RAM request；GPU 測試與 CPU Worker 分批執行 |
| 清理誤刪正式資料 | 使用專用 namespace；保留 PVC，**不**使用未審核的 `kubectl delete namespace` 清理正式叢集 |

建議在每個 Phase 結束時保留 `kubectl get all -n openmot-test -o wide`、PVC/Events 與 logs；清理前先備份測試證據與 Redis 資料，再依資源清單逐項刪除。

## 10. 參考資料

- Kubernetes GPU Scheduling：<https://kubernetes.io/docs/tasks/manage-gpus/scheduling-gpus/>
- k3s NVIDIA Container Runtime：<https://docs.k3s.io/advanced#nvidia-container-runtime>
- Kubernetes Probes：<https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/#container-probes>
- 專案既有執行手冊：`docs/kubernetes.md`（需以實際儲存庫內容確認）

---

## 11. v0.4 新增 Phase 6：Kubernetes Monitoring Manifests（單機 k3s）

> 對應需求：`Kubernetes monitoring manifests (ServiceMonitor, monitoring namespace)`，README 已勾選，對應的 change `add-kubernetes-monitoring-manifests` 已封存。
>
> - **已完成**：清單已寫好並納入版本控制，通過 render、schema 與結構測試。
> - **未完成**：沒有任何一項在 k3s 叢集上實際驗證（沒有可用的叢集），實機驗證由 `verify-kubernetes-on-cluster` 承接。
>
> 先前 Compose 的 Prometheus/Grafana 不會自動變成 k3s 監控服務。

### 11.1 目標、前置條件與範圍

- 在 **同一台單節點 k3s 主機**建立 `monitoring` namespace；業務應用在 `openmot` namespace（k3s overlay 的實際值；本計畫第 1–10 節的 `openmot-test` 只是測試用範例名稱）。
- 在 `monitoring` 中部署 Prometheus Operator、Prometheus、Grafana（建議使用 `kube-prometheus-stack` Helm Chart，依資源情況調整組件）。
- 建立 API（必要）與 Worker 的 Kubernetes metrics Service，以及對應 `ServiceMonitor`（**已撰寫**，見 11.4）。
- 利用 Prometheus Targets、PromQL 和 Grafana 確認 metrics 可見；驗證監控目標異常時 Prometheus 可觀測到 `up=0` 或 target 消失（須依服務發現及部署方式判定）。
- 驗證原有 Alert Rules 能在 Kubernetes 監控設定中載入，並進行可控的規則／告警測試；**Alertmanager 並非 v0.4 已完成的必要條件**，若要告警通知則另外規劃。
- 即使沒有 NVIDIA GPU，也能測 API、Prometheus、Grafana 與大部分 Kubernetes 監控功能；GPU Worker 專屬 metrics 的實機測試仍標 `BLOCKED`。

**注意**：`ServiceMonitor` 是 Prometheus Operator 的 CRD，**只建立 ServiceMonitor YAML，未安裝 CRD/Operator/Prometheus，不會產生可運作的指標採集**。還要確認 `ServiceMonitor` 的 label 與 Prometheus 的 `serviceMonitorSelector` 匹配，以及 ServiceMonitor 所在 namespace 與 `serviceMonitorNamespaceSelector` 匹配。`ServiceMonitor.spec.endpoints.port` 使用 **Service 端口名稱**，不是 Pod 容器的端口號。

### 11.2 計畫表

圖例：✅ 已完成、🟡 部分完成（說明於欄內）、➖ 不在本專案範圍、☐ 未做。「清單／設定」欄指儲存庫裡的檔案與測試；「實機驗證」欄指在 k3s 叢集上的結果，**目前全部未做**。

| ID | 階段 | 工作項目 | 前置條件 | 可驗收結果 | 清單／設定 | 實機驗證 |
|---|---|---|---|---|---|---|
| M-01 | 6.0 | 確認 k3s Node Ready、資源餘量與業務 `/metrics` | Phase 0–2 | API metrics 可取得、節點可排程 | ➖ 非清單項目 | ☐ TODO |
| M-02 | 6.1 | 建立 `monitoring` namespace | M-01 | `kubectl get ns monitoring` 成功 | ✅ `deploy/k8s/monitoring/namespace.yaml` | ☐ TODO |
| M-03 | 6.1 | 安裝 Prometheus Operator / CRDs / Prometheus / Grafana | M-02 | `servicemonitors.monitoring.coreos.com` 存在、監控 Pod Ready | ➖ 前置條件，本專案不安裝（見 `docs/kubernetes.md`）；儲存庫沒有 Helm values | ☐ TODO |
| M-04 | 6.2 | API/Worker Service 曝露命名 metrics port | M-01 | Service 可在叢集內存取 `/metrics` | ✅ base 的 `metrics-services.yaml`：`api-metrics`（9101）、`worker-metrics`（9102） | ☐ TODO |
| M-05 | 6.2 | 建立 `ServiceMonitor` YAML、Kustomize 整合 | M-03、M-04 | `kubectl get servicemonitor -A` 可見 | ✅ `servicemonitor-api.yaml`、`servicemonitor-worker.yaml`、`kustomization.yaml` | ☐ TODO |
| M-06 | 6.2 | 檢查 `serviceMonitorSelector` 與 namespace selector | M-05 | Prometheus 正確發現 API/Worker targets | 🟡 `release: monitoring` 是未驗證的假設；覆寫方式已寫入 `docs/kubernetes.md` | ☐ TODO |
| M-07 | 6.3 | 驗證 API latency、failure、throughput 指標 | M-06 | PromQL 有時間序列且可更新 | 🟡 api metrics 已在 Compose 端對端驗證，Kubernetes 未驗證 | ☐ TODO |
| M-08 | 6.3 | 驗證 Worker job duration、failure、queue depth 指標 | M-06 | 可產生對應測試任務並查詢 metrics；GPU 限制另記 | 🟡 worker metrics 只在程序內測過 | ☐ TODO（GPU 部分 BLOCKED） |
| M-09 | 6.4 | 匯入／核對 Grafana Dashboard | M-07 | 面板成功查到 Prometheus 資料 | 🟡 dashboard 只在 Compose overlay 自動載入，沒有 Kubernetes 載入方式 | ☐ TODO |
| M-10 | 6.4 | 部署、驗證原有 Alert Rules | M-07 | 規則被 Prometheus 載入且可觀察狀態 | ✅ `prometheusrule.yaml` 與 `monitoring/rules/openmot.rules.yml` 相同（測試保證）；規則以 promtool 單元測試 | ☐ TODO |
| M-11 | 6.5 | 故障注入與恢復 | M-09、M-10 | 監控能察覺目標異常並在恢復後更新 | ☐ 未做 | ☐ TODO |
| M-12 | 6.5 | 保存 Metrics、Pod Logs、Events、截圖與操作紀錄 | 全部 | 形成可重現驗收紀錄；文件更新 | ☐ 未做 | ☐ TODO |

### 11.3 檔案結構（目前實際）

```text
deploy/k8s/
├── base/
│   ├── api.yaml / worker.yaml / redis.yaml / storage.yaml
│   ├── metrics-services.yaml      # api-metrics（9101）、worker-metrics（9102），只給 Prometheus
│   └── kustomization.yaml
├── overlays/k3s-single-node/
└── monitoring/                    # 單獨套用，需要 Operator 的 CRD
    ├── namespace.yaml
    ├── servicemonitor-api.yaml
    ├── servicemonitor-worker.yaml
    ├── prometheusrule.yaml
    └── kustomization.yaml
```

儲存庫沒有 `helm/monitoring-values.yaml`：Prometheus Operator 與 Grafana 的安裝不屬於本專案，要用 `kube-prometheus-stack` 時需自行建立 values。

### 11.4 實際清單（取代原先的示例）

清單已在儲存庫裡，以實際檔案為準：

| 檔案 | 內容 |
|---|---|
| `deploy/k8s/base/metrics-services.yaml` | `api-metrics`（埠 9101）與 `worker-metrics`（埠 9102）兩個 ClusterIP Service，只給 Prometheus 用；具名埠 `metrics`，`publishNotReadyAddresses: true` |
| `deploy/k8s/monitoring/servicemonitor-api.yaml`、`servicemonitor-worker.yaml` | 放在 `monitoring` namespace，選取 `openmot` namespace 的 metrics Service，並把 `job` 固定為 `openmot-api`、`openmot-worker` |
| `deploy/k8s/monitoring/prometheusrule.yaml` | 與 `monitoring/rules/openmot.rules.yml` 相同的警報規則 |
| `deploy/k8s/monitoring/namespace.yaml` | `monitoring` namespace |

與本計畫原先示例的差異，執行時以「實際」欄為準：

| 項目 | 原先示例 | 實際 |
|---|---|---|
| metrics 埠 | 與 API 共用 8000 | 獨立埠 9101／9102，不經公開的 `api` Service |
| 業務 namespace | `openmot-test` | `openmot`（k3s overlay） |
| 清單路徑 | `k8s/monitoring/` | `deploy/k8s/monitoring/` |
| ServiceMonitor 的 selector | `app: api` | `app.kubernetes.io/name: api-metrics`、`worker-metrics` |

**選取器重要提醒**：當使用 `kube-prometheus-stack` 時，Helm release 名稱、chart values、Operator/Prometheus 的選取器會影響是否抓到 ServiceMonitor。清單帶的 `release: monitoring` 只是**假設** release 名稱為 `monitoring`，尚未在叢集上驗證。不能只看 `kubectl apply` 成功就宣稱採集成功。

### 11.5 執行與診斷指令

```bash
# 先確定 Node、namespace 與應用狀況
kubectl get nodes -o wide
kubectl get pods,svc -n openmot

# 安裝 Operator（不屬於本專案；values 需自行建立並審查資源、retention 與 selector）
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update
helm upgrade --install monitoring prometheus-community/kube-prometheus-stack \
  --namespace monitoring --create-namespace \
  --values <your-monitoring-values.yaml>
kubectl get crd servicemonitors.monitoring.coreos.com prometheusrules.monitoring.coreos.com

# 先套用工作負載 overlay，再單獨套用監控清單
kubectl apply -k deploy/k8s/overlays/k3s-single-node
kubectl apply -k deploy/k8s/monitoring

# 檢查 ServiceMonitor 能否選到 Service
kubectl get servicemonitor,prometheusrule -n monitoring
kubectl -n monitoring describe servicemonitor api-metrics
kubectl -n openmot get svc api-metrics worker-metrics --show-labels
kubectl get prometheus -A -o yaml

# 確認 metrics Service 本身可回 metrics（本機終端開啟通道）
kubectl -n openmot port-forward svc/api-metrics 19101:9101
# 在另一個終端：curl -fsS http://127.0.0.1:19101/metrics

# Prometheus / Grafana 的實際 Service 名稱以 kubectl 查詢為準
kubectl -n monitoring get svc
```

套用工作負載 overlay 會讓 api 滾動更新、worker 重建（策略是 `Recreate`，會中斷正在處理的 Job，Job 會重新投遞），請在 worker 閒置時套用。

### 11.6 實機驗收案例

MT-01～MT-08 **全部尚未執行**（沒有叢集可用），結果欄一律視為 ☐ 未做；沒有 GPU 時，MT-05 只能做到不含 GPU 推論的部分，其餘標 `BLOCKED`。

| 測試 | 操作 | PASS 判準 |
|---|---|---|
| MT-01 CRD | `kubectl get crd servicemonitors.monitoring.coreos.com` | CRD 存在 |
| MT-02 Namespace | `kubectl get pods -n monitoring` | Prometheus/Operator/Grafana 主要服務 Ready |
| MT-03 Discovery | 檢查 Prometheus Targets／Operator selectors | API target 出現，且 `up{...}=1`（以實際 labels 查詢） |
| MT-04 Metrics | 產生 API 請求並查 PromQL | latency／requests／failures 指標隨事件更新，名稱依現有程式為準 |
| MT-05 Worker | 送測試工作、查看 worker metrics | 工作指標更新；需符合 Worker metrics 輸出模式 |
| MT-06 Dashboard | Grafana 查詢 Prometheus data source | 儀表板可視化數值與 PromQL 一致 |
| MT-07 Rule | 查看 Prometheus Rules、進行可控告警測試 | 規則已載入且 firing/pending 行為符合設定 |
| MT-08 Recovery | 暫停/重啟 API 或關閉 metrics endpoint | 能記錄 target 可用性變化及恢復；不聲稱單機高可用 |

**Worker 注意**：worker 的 metrics 由常駐的 HTTP 伺服器在埠 9102 提供，base 有對應的 `worker-metrics` Service 與 ServiceMonitor，所以不需要改用其他監控方案。但 worker metrics 目前只在程序內測過，且 worker 處理 Job 時會因 readiness 失敗而 NotReady；清單以 `publishNotReadyAddresses: true` 處理，這個設計是否讓 Prometheus 在 NotReady 時仍抓得到目標，也要靠實機確認（MT-03、MT-05）。

### 11.7 完成 v0.4 最後一項的條件

- [x] `monitoring` namespace 與部署設定納入版本控制（`deploy/k8s/monitoring/`）。
- [x] ServiceMonitor YAML 納入 k3s Kustomize 管理（沒有 Helm）。
- [ ] API target 已被 Prometheus 發現並成功抓取 `/metrics`。
- [ ] Worker 監控方案已明確（常駐埠 9102 加 ServiceMonitor）；至少完成可行環境的驗證（不具 GPU 部分註明限制）仍未做。
- [ ] Grafana 能以 Kubernetes 環境中的 Prometheus 顯示既有指標（目前沒有 Kubernetes 的 dashboard 載入方式）。
- [ ] 原有規則在 Kubernetes 環境已載入並測試（`PrometheusRule` 已寫好，未載入）。
- [ ] 記錄 chart/CRD 版本、操作流程、截圖、查詢結果與未完成限制。

> **目前結論**：v0.4 的驗收定義採「manifests」：配置完成（README 已勾選、change 已封存）。若要宣稱 `observability` 在 Kubernetes 上真正可用，仍需完成實際 k3s 的 MT-01～08；上面未勾選的項目就是這部分。切勿將 Compose 通過視為 Kubernetes 通過。

### 11.8 參考文件

- Prometheus Operator ServiceMonitor：<https://prometheus-operator.dev/docs/platform/troubleshooting/>
- Prometheus Operator 指標監控入門：<https://github.com/prometheus-operator/prometheus-operator/blob/main/Documentation/developer/getting-started.md>
- kube-prometheus-stack Helm Chart values：<https://github.com/prometheus-community/helm-charts/blob/main/charts/kube-prometheus-stack/values.yaml>
