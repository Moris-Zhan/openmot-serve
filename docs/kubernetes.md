# Kubernetes 部署（k3s 單節點）

這份文件說明如何把 api、GPU worker 與 Redis 部署到一台有 NVIDIA GPU 的單節點 k3s 叢集。清單在 `deploy/k8s/`：`base/` 與叢集無關，`overlays/k3s-single-node/` 加上單節點特有的設定（Namespace `openmot`、NodePort 30080、`local-path` 儲存、權重路徑與映像標籤）。

> **未經實機驗證。** 目前沒有可用的 k3s 環境（沒有宿主機或獨立 VM，Docker daemon 上的特權 k3s 受節點 inotify 限制無法啟動），因此本文的指令尚未在真實叢集上執行過。清單本身有渲染測試與 schema 檢查，但映像載入、GPU 排程與 Pod 重建後的復原都需要在外部叢集依本文逐步驗證（對應任務 4.1，待外部叢集執行）。

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
