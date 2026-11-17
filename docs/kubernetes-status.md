# Kubernetes 部署狀態快照

> 日期：2026-11-17｜commit：`903bf5f`（`main`）
>
> 這是「現況」的快照，會隨時間過時；部署步驟、設定值與疑難排解請看 [`kubernetes.md`](kubernetes.md)，這裡不重複。

## 一句話結論

專案的 Kubernetes 清單已經寫好並通過渲染與 schema 測試，但**從未套用到任何叢集**，因此 GPU 排程、Pod 重建後的復原、監控採集都**尚未驗證**。這台開發機器本身是別人叢集裡的一個 Pod，不能拿來跑 k3s。

## 這台機器的環境事實

| 項目 | 狀態 |
| --- | --- |
| 型態 | 一個 Kubernetes Pod（主機名 `moris-conda-…`），不是獨立主機或 VM |
| 核心 | `6.8.0-100-generic` |
| GPU | NVIDIA GeForce RTX 5090，驅動 `580.126.20`（可由 `nvidia-smi` 看到） |
| k3s | 未安裝（沒有 `k3s` 執行檔，也沒有 `/etc/rancher`） |
| kubeconfig | 沒有（`~/.kube` 只有 `cache/`，`kubectl` 沒有 current-context） |
| 權限 | 容器內 root，但缺少 `cap_net_admin` 等能力，不能啟動 k3s、也不能調整節點 sysctl |
| Docker | Docker daemon 可用，上面跑的是 CVAT 與 nuclio，不是 k3s |
| 驗證工具 | `kubectl` v1.31.4、`kustomize` v5.5.0、`kubeconform` v0.6.7（安裝在 `~/.local/bin`） |
| Helm | 未安裝 |

這個 Pod 帶有它所在叢集的 serviceaccount token，但那個叢集**不是專案的部署目標**，也不是我們建的，這份快照沒有去查它，驗證時也不得把 `KUBECONFIG` 指向它。

## 清單盤點（`deploy/k8s/`）

| 資源 | 型態 | 重點 |
| --- | --- | --- |
| api | Deployment（1 副本）與 ClusterIP Service（`http:8000`） | startup、readiness、liveness 探針；`RuntimeDefault` seccomp |
| worker | Deployment（1 副本，`Recreate`） | `nvidia.com/gpu: 1`、`runtimeClassName: nvidia`、startup 與 readiness 探針、**沒有 livenessProbe**（依設計）、`terminationGracePeriodSeconds: 1560`、唯讀的權重 hostPath |
| redis | StatefulSet（1 副本）與 Service | `--appendonly yes`（AOF）、1Gi 的 volumeClaimTemplate、readiness 與 liveness |
| 共用儲存 | PVC `openmot-storage`（20Gi） | api 與 worker 共用 |
| 設定 | ConfigMap `openmot-config`（kustomize 產生） | 精度、上限與逾時等 |
| k3s overlay | `overlays/k3s-single-node/` | Namespace `openmot`、api 改為 NodePort `30080`、`local-path` 儲存類別、權重路徑、映像標籤 `dev` |

api 與 worker 另有 metrics 埠（`9101`、`9102`），目前**沒有**對應的 Service 埠與 `ServiceMonitor`。

## 已驗證與未驗證

| 項目 | 狀態 | 依據或原因 |
| --- | --- | --- |
| 清單可由 kustomize 渲染 | 已驗證 | 渲染測試 |
| 渲染結果符合 Kubernetes 1.31.0 的 schema | 已驗證 | kubeconform |
| 清單的結構性質（探針、資源、安全設定、GPU 請求、Recreate 等） | 已驗證 | 測試並用突變確認會失敗；2026-11-17 重跑 9 個 k8s 相關測試檔共 75 項通過 |
| 映像載入 k3s 的 containerd | 未驗證 | 沒有叢集 |
| api 與 Redis 變 Ready、經 Service 呼叫 `/health` | 未驗證 | 沒有叢集 |
| GPU 排程（`nvidia.com/gpu`、Device Plugin、`runtimeClassName: nvidia`） | 未驗證 | 沒有叢集；NVIDIA GPU Operator 不在範圍，假設使用 Device Plugin |
| 無 GPU 時 worker 維持 Pending | 未驗證 | 沒有叢集 |
| Job 端對端結果與 CLI 逐位元組相同 | 未驗證 | 沒有叢集 |
| 刪除 worker、api、Redis 的 Pod 後復原 | 未驗證 | 沒有叢集 |
| Redis 資料經 PVC 在 Pod 重建後保留 | 未驗證 | 沒有叢集 |
| 資源值（requests 與 limits） | 未校正 | 目前是估計值 |
| Prometheus 抓取 api 與 worker | 未驗證，且清單尚未存在 | 見下方 |

README 路線圖 v0.3 有兩項保持未勾選：「GPU scheduling for the worker」與「Verify the deployment, GPU scheduling and pod recovery on a live k3s cluster」。

## 阻塞原因

1. **沒有可用的叢集。** 這台機器是 Pod，沒有權限啟動 k3s。先前在 Docker daemon 上嘗試特權 k3s，因節點的 `fs.inotify.max_user_instances=128` 導致 CNI 設定監看失敗、節點無法註冊。
2. **GPU 路徑要有真實的 GPU 節點。** 這台機器看得到 RTX 5090，但沒有叢集可以安裝 Device Plugin 與 NVIDIA Container Toolkit 的 k3s 設定。

## 相關的 OpenSpec change

| change | 進度 | 說明 |
| --- | --- | --- |
| `verify-kubernetes-on-cluster` | 0/16 | 實機驗證：k3s 與 GPU 環境、冷部署、端對端、Pod 重建、資源校正，第 4 節為監控實機驗收；**阻塞**，需要使用者提供宿主機或獨立 VM 與 kubeconfig |
| `add-kubernetes-monitoring-manifests` | 尚未提案 | 已規劃：`deploy/k8s/monitoring/`（`monitoring` namespace、兩個 `ServiceMonitor`、`PrometheusRule`）、base 補 metrics 埠與 worker Service、用 `relabelings` 讓 `job` 標籤對上現有規則；非目標是 Grafana 儀表板、Alertmanager、Helm chart 與實機驗證 |

## 解除阻塞需要什麼

1. 一台由使用者提供、可以安裝 k3s 的 Linux 宿主機或獨立 VM，並提供 kubeconfig。開始前先確認 `KUBECONFIG` 指向的是這個叢集。
2. 要驗證 GPU 路徑，該節點需要 NVIDIA 驅動、Container Toolkit 與 Device Plugin；節點沒有 GPU 時，只能宣告 api、Redis 與 Pod 重建這些不需要 GPU 的部分已驗證，兩個含 GPU 的 v0.3 項目仍保持未勾選。
3. 要驗證監控，叢集需先安裝 Prometheus Operator（含 `servicemonitors.monitoring.coreos.com` CRD）與 Prometheus。

## 單機的限制

單節點 k3s 只能驗證 Pod 重建與容器重啟，不能證明跨節點容錯；整台主機故障時沒有其他節點接手。`local-path` 儲存是節點本機磁碟，「Pod 重建後資料還在」不等於「主機或磁碟故障後資料還在」。
