# Proposal

## Why

v0.2 已經能用 Docker Compose 在一台機器上啟動 api、GPU worker 與 Redis，但 compose 沒有宣告式的探針（probe）、資源上限、排程與自我修復的語意，也不是專案路線圖要求的部署形式。專案的目標是「把多目標追蹤做成生產服務」，路線圖 v0.3 要的是 k3s 單機叢集上的部署：容器掛掉要被重新拉起、GPU 要由排程器分配、狀態要在 Pod 重建後保留。v0.4 的 CI/CD 與觀測也需要一組可套用的 Kubernetes 清單（manifests）作為部署單元。

## What Changes

- 新增 `deploy/k8s/`：以純 YAML 搭配 kustomize 組織的清單，包含 namespace、ConfigMap、Redis（StatefulSet，AOF 加 PVC）、api（Deployment 加 Service）、worker（Deployment，請求 `nvidia.com/gpu: 1`）、共用的 PVC，以及一個適用單節點 k3s 的 overlay。
- 為每個工作負載宣告 startup／readiness／liveness 探針與 CPU、記憶體的 requests 與 limits；worker 的優雅終止時間要涵蓋單一 Job 的時間上限。
- api 與 worker 共用一個單節點的 PVC（local-path，ReadWriteOnce），依靠 local-path 卷的節點親和讓兩者落在同一節點；權重不進映像，以唯讀掛載提供；Pod 以非 root（uid 1000）執行。
- 在 k3s 單機叢集上實際部署並做端到端驗證：提交真實 MOT17 影格 zip、輪詢到 `succeeded`、結果與 CLI 輸出逐位元組相同；刪除 worker Pod 後 Job 仍能完成；重建 api 與 worker 後既有 Job 仍可取回。
- 先評估能否取得可安裝 k3s 的環境（宿主機或獨立 VM）。目前的開發環境是沒有特權的容器，且 Docker daemon 在另一個容器；不在現有容器內強行安裝。環境不可行時，保留清單、完成靜態驗證，並提供在外部 k3s 叢集部署的完整指令與驗證步驟。
- 新增部署文件（繁體中文）：叢集前置條件、映像載入、GPU 裝置外掛、套用、驗證與疑難排解。
- 不包含：GPU Operator 的安裝與管理（只在文件說明前置條件）、Helm chart、Ingress 與 TLS、多節點與 ReadWriteMany 或 S3 儲存、HorizontalPodAutoscaler、NetworkPolicy、Prometheus 與 Grafana（v0.4）、CI（v0.4）、水平擴充 worker。

## Capabilities

### New Capabilities
- `kubernetes-deployment`: 以 kustomize 清單在單節點 k3s 上部署 api、GPU worker 與 Redis，包含探針、資源、共用儲存、GPU 排程與重建後的狀態保留。

### Modified Capabilities

（無。`container-deployment` 的 compose 行為不變，Kubernetes 是並行的部署形式；API 與 worker 的程式行為不變。）

## Impact

- **新增檔案**：`deploy/k8s/`、驗證清單的測試、`docs/` 下的 Kubernetes 部署文件；README 路線圖 v0.3 與部署架構章節對應更新。
- **程式碼**：預期不修改 `app/`、`worker/`、`jobs/`、`tracking/`。若驗證發現清單需要的程式支援（例如收到 SIGTERM 時的行為），另行評估並在設計中記錄。
- **新增工具**：kubectl 與 kustomize（驗證用，不進映像）；實機驗證需要 k3s 與 GPU 裝置外掛。
- **外部系統**：k3s、NVIDIA 驅動與 Container Toolkit、NVIDIA device plugin。
- **風險**：在目前沒有特權的容器環境無法直接安裝 k3s，實機驗證取決於能否取得宿主機或獨立 VM（或在 Docker daemon 上以特權容器執行 k3s，GPU 直通尚未驗證）；映像要先進到叢集的容器執行階段（k3s 的 containerd）；單一 GPU 下滾動更新會讓新舊 worker 搶同一張卡，需要使用 Recreate 策略。
