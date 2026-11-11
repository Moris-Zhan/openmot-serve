# Kubernetes 部署（k3s 單節點）

這份文件說明如何把 api、GPU worker 與 Redis 部署到一台有 NVIDIA GPU 的單節點 k3s 叢集。清單在 `deploy/k8s/`：`base/` 與叢集無關，`overlays/k3s-single-node/` 加上單節點特有的設定（Namespace `openmot`、NodePort 30080、`local-path` 儲存、權重路徑與映像標籤）。

> **未經實機驗證。** 目前沒有可用的 k3s 環境（沒有宿主機或獨立 VM，Docker daemon 上的特權 k3s 受節點 inotify 限制無法啟動），因此本文的指令尚未在真實叢集上執行過。清單本身有渲染測試與 schema 檢查，但映像載入、GPU 排程與 Pod 重建後的復原都需要在外部叢集依本文逐步驗證（對應任務 4.1，待外部叢集執行）。

## 驗證環境

### 驗證工具

開發環境沒有 root 權限的套件管理，三個工具以固定版本安裝到使用者目錄 `~/.local/bin`（linux/amd64），下載後都比對過官方公布的 SHA-256：

| 工具 | 版本 | 來源 | 用途 |
| --- | --- | --- | --- |
| kubectl | v1.31.4 | `https://dl.k8s.io/release/v1.31.4/bin/linux/amd64/kubectl` | 對叢集下指令；`kubectl apply -k` 內建的 kustomize 是 v5.4.2 |
| kustomize | v5.5.0 | `https://github.com/kubernetes-sigs/kustomize/releases/tag/kustomize%2Fv5.5.0` | 渲染 `deploy/k8s/` 的清單，測試使用這一個 |
| kubeconform | v0.6.7 | `https://github.com/yannh/kubeconform/releases/tag/v0.6.7` | 以 Kubernetes 1.31.0 的 schema 檢查渲染結果 |

確認三者可以執行，輸出應包含上表的版本：

```shell
kubectl version --client
kustomize version
kubeconform -v
```

### 沒有叢集的原因與選定的層級

驗證分三層：(1) 無叢集，只做渲染與 schema 檢查；(2) 有 k3s 但沒有 GPU；(3) 有 k3s 與 GPU 的完整端到端。**本次只達到第 1 層（無叢集）**，原因如下：

- 沒有可用的宿主機或獨立 VM。
- 開發環境本身是 Kubernetes 叢集（namespace `ai-model`）中的一個 Pod，Docker daemon 是同一個 Pod 的 sidecar，不是獨立的宿主機。
- 在這個 Docker daemon 上以特權容器啟動 k3s（`rancher/k3s:v1.31.4-k3s1`）失敗：containerd 的 CRI 啟動時出現 `failed to create fsnotify watcher: too many open files`，節點的 `fs.inotify.max_user_instances` 為 128，而且這個限制無法在 Pod 內調整。
- `ai-model` 是共用的 GPU 開發叢集，沒有提供 kubeconfig，也不列為部署目標。

第 1 層驗證的是清單的結構與設定值（`pytest` 預設會跑）；第 2、3 層，包含 GPU 排程、探針行為、Pod 重建後的復原，**未經實機驗證**，需要在外部 k3s 叢集依下方的步驟執行。

## 部署

以下步驟在 k3s 節點上執行，目錄是這個 repository 的根目錄。

### 1. 把映像載入 k3s 的 containerd

叢集不連任何 registry，清單使用 `imagePullPolicy: IfNotPresent` 與本機標籤 `dev`。先在能建置映像的機器上建置（見 `docs/service.md` 的「容器部署」），再把映像匯入 k3s：

```shell
docker save openmot-api:dev openmot-worker:dev | sudo k3s ctr images import -
```

worker 映像約 11.4 GB，匯入需要一些時間與足夠的磁碟空間。映像不在這台節點上建置時，先 `docker save` 成檔案傳到節點，再用 `sudo k3s ctr images import <檔案>` 匯入。

確認 kubelet 看得到這兩個映像：

```shell
sudo k3s crictl images | grep openmot
```

預期看到 `openmot-api` 與 `openmot-worker` 各一列，標籤為 `dev`。看不到時，Pod 會停在 `ErrImagePull` 或 `ImagePullBackOff`。

### 2. 放置權重檔

權重不進映像，也不進清單，由 worker 從節點上的檔案唯讀掛入。overlay 指定的節點路徑是 `/var/lib/openmot/models/yolox_x.pth`：

```shell
sudo install -D -m 0644 models/yolox_x.pth /var/lib/openmot/models/yolox_x.pth
sha256sum /var/lib/openmot/models/yolox_x.pth
```

預期的 SHA-256 是：

```
5652330b6ae860043f091b8f550a60c10e1129f416edfdb65c259be6caf355cf
```

worker 啟動時會再驗證一次，不符就拒絕啟動。檔案權限要讓 uid 1000 能讀取（上面的 `0644` 即可）。路徑綁定這台節點，多節點叢集需要改用其他方式。
