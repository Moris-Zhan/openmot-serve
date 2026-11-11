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

## 前置條件

下列項目不由這個 repository 的清單管理，要先在節點與叢集上備妥（**未經實機驗證**）：

1. **NVIDIA 驅動**：RTX 5090 需要支援 CUDA 12.8 以上的驅動。在節點上 `nvidia-smi` 要能看到 GPU。
2. **NVIDIA Container Toolkit**：讓 containerd 能用 `nvidia` runtime 啟動容器。
3. **k3s 單節點叢集**：k3s 偵測到 NVIDIA runtime 後會把它設定給 containerd。清單用 `runtimeClassName: nvidia`，所以叢集裡要有名為 `nvidia` 的 RuntimeClass：
   ```shell
   kubectl get runtimeclass nvidia
   ```
   沒有時依 k3s 與 NVIDIA 的官方文件建立 `handler: nvidia` 的 RuntimeClass。
4. **NVIDIA 裝置外掛（device plugin）**：依 NVIDIA k8s-device-plugin 的官方文件安裝。**本專案不安裝也不管理 GPU Operator 或裝置外掛。** 裝好後節點要顯示可分配的 GPU：
   ```shell
   kubectl describe node | grep nvidia.com/gpu
   ```
   預期 `Capacity` 與 `Allocatable` 都有 `nvidia.com/gpu: 1`。
5. **kubectl 連得到叢集**：在 k3s 節點上通常是 `export KUBECONFIG=/etc/rancher/k3s/k3s.yaml`。

確認節點是 `Ready`：

```shell
kubectl get nodes
```

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

## 套用

清單套用到專屬的 Namespace `openmot`，一個指令建立全部資源：

```shell
kubectl apply -k deploy/k8s/overlays/k3s-single-node
```

等三個工作負載就緒：

```shell
kubectl -n openmot rollout status statefulset/redis
kubectl -n openmot wait --for=condition=available deployment/api --timeout=300s
kubectl -n openmot wait --for=condition=available deployment/worker --timeout=600s
```

worker 啟動時要通過環境檢查並載入偵測器，所以較慢。經 NodePort 呼叫 `/health`，預期回 `200`（把 `<節點 IP>` 換成節點位址）：

```shell
curl -s -o /dev/null -w '%{http_code}\n' http://<節點 IP>:30080/health
```

之後提交 Job、查詢與取回結果的方式與其他部署相同，見 `docs/service.md`，只是位址換成 `http://<節點 IP>:30080`。

> worker 在處理長 Job 時，`kubectl get pods` 可能顯示 `0/1`（NotReady）：worker 使用 Celery solo pool，忙碌時無法回應健康檢查。這是預期行為，worker 不在任何 Service 後面，不影響流量；判斷是否正常看 Pod 沒有重啟，以及 Job 的狀態。

## 設定值

所有設定來自 `deploy/k8s/base/kustomization.yaml` 的 `configMapGenerator`，名稱與預設值和 compose 部署相同（見 `docs/service.md` 的環境變數表）：

| 變數 | 預設值 | 說明 |
| --- | --- | --- |
| `OPENMOT_STORAGE_DIR` | `/data/storage` | api 與 worker 共用的儲存目錄，掛載共用 PVC |
| `OPENMOT_REDIS_URL` | `redis://redis:6379/0` | 佇列與 Job 狀態；`redis` 是同 Namespace 的 headless Service |
| `OPENMOT_WEIGHTS` | `/models/yolox_x.pth` | worker 內的權重路徑，需與 volumeMount 一致 |
| `OPENMOT_PRECISION` | `fp16` | 偵測精度 |
| `OPENMOT_MAX_UPLOAD_BYTES` | `1073741824` | 單次上傳大小上限（1 GiB） |
| `OPENMOT_MAX_UNPACKED_BYTES` | `4294967296` | 解壓後大小上限（4 GiB） |
| `OPENMOT_LOCK_TIMEOUT_SECONDS` | `1800` | Job 鎖逾時，也是訊息重新投遞的等待時間 |
| `OPENMOT_TASK_TIME_LIMIT_SECONDS` | `1500` | 單一 Job 的時間上限，必須小於鎖逾時 |

修改時改 `configMapGenerator` 的字面值再重新 `kubectl apply -k`。ConfigMap 名稱帶有內容雜湊，內容變動會產生新名稱並觸發 Pod 滾動，不會出現「改了設定但 Pod 沒重啟」。`OPENMOT_WEIGHTS` 要與 worker 的掛載路徑一起改。`terminationGracePeriodSeconds`（1560 秒）是以 1500 秒的時間上限加 60 秒緩衝設定的，調高時間上限時也要調高它。

設定值無效時，對應的 Pod 會啟動失敗並在日誌指出是哪個變數，例如：

```shell
kubectl -n openmot logs deployment/worker
```

## 儲存與資料保存

| 資料 | 儲存 | 說明 |
| --- | --- | --- |
| 序列與 Result | PVC `openmot-storage`（20Gi、`ReadWriteOnce`、`local-path`） | api 與 worker 共用 |
| Job Status 與佇列 | Redis 自己的 PVC（`volumeClaimTemplates`，1Gi，AOF） | 重建 Pod 後保留 |

k3s 的 `local-path` 供應器建立的 PV 帶有節點親和，所以兩個 Pod 必然落在同一個節點，單節點下不需要另外設定 podAffinity。重建任何一個 Pod 都不會遺失已完成 Job 的狀態與 Result。

**移除工作負載與移除資料是不同的事：**

```shell
# 只移除工作負載，PVC 與資料保留；再次 apply 即可恢復
kubectl -n openmot delete deployment/api deployment/worker

# 連 Namespace、PVC 與資料一起移除，無法復原
kubectl delete -k deploy/k8s/overlays/k3s-single-node
```

`kubectl delete -k` 會刪除整個 Namespace，其中的 PVC 與 `local-path` 的 PV 也會被回收，全部 Job 資料就消失了。不要把它當成一般的重啟或復原手段。`local-path` 沒有備份機制，備份不在這個變更的範圍內。

## 疑難排解

| 症狀 | 原因與處理 |
| --- | --- |
| Pod 停在 `ErrImagePull` 或 `ImagePullBackOff` | 映像沒有載入 k3s 的 containerd。依「部署」的步驟 1 匯入，並用 `sudo k3s crictl images \| grep openmot` 確認；標籤要是 `dev`。 |
| worker 一直 `Pending`，事件是 `Insufficient nvidia.com/gpu` | 節點沒有可分配的 GPU。用 `kubectl describe node \| grep nvidia.com/gpu` 確認裝置外掛已運作；GPU 已被其他 Pod 占用時也會這樣。`kubectl -n openmot describe pod -l app.kubernetes.io/name=worker` 看事件。 |
| worker `CrashLoopBackOff` | 看 `kubectl -n openmot logs deployment/worker`：沒有 GPU 時環境檢查會失敗並以非 0 結束；權重 SHA-256 不符或路徑不存在也會。 |
| worker 的 `kubectl get pods` 顯示 `0/1` | 處理 Job 時暫時 `NotReady` 是預期行為（見「套用」的說明）。先確認重啟次數是 0。 |
| api 或 worker 日誌出現 `Permission denied` 寫入 `/data/storage` | PVC 目錄的擁有者與 uid 1000 不符。Pod 設定了 `fsGroup: 1000`，仍失敗時檢查節點上 `local-path` 的目錄權限。 |
| `/health` 回 `503` | api 連不到 Redis 或儲存。`kubectl -n openmot get pods` 看 Redis 是否就緒；api 此時會從 Service 端點移除，但不會被重啟。 |
| 刪掉 worker 後 Job 停在 `running` | 強制刪除（`--grace-period=0 --force`）時沒有暖關閉，訊息要等鎖逾時（預設 1800 秒）才會重新投遞。一般的 `kubectl delete pod` 會讓 worker 先做完目前的 Job。 |

## 外部叢集驗證手冊

下面的步驟尚未在任何叢集上執行過（對應任務 4.1 到 4.5），在有 k3s 與 GPU 的外部叢集依序執行，並把結果填回這份文件。

### 4.1 冷部署

完成「前置條件」「部署」「套用」後：

```shell
kubectl -n openmot get pods
```

預期 `redis-0`、`api-*`、`worker-*` 都是 `Running`，`/health` 回 `200`。沒有 GPU 的叢集預期是：worker 維持 `Pending`，事件為 `Insufficient nvidia.com/gpu`，api 與 Redis 不受影響。

### 4.2 端對端結果與 CLI 逐位元組相同

用 MOT17-09-FRCNN 的 525 張影格打包後提交（打包方式見 `docs/service.md`），輪詢到 `succeeded` 後取回結果，與命令列的輸出比對：

```shell
python -m tracking.run data/MOT17/train/MOT17-09-FRCNN/img1 --output /tmp/cli.txt
cmp /tmp/cli.txt /tmp/k8s.txt
```

預期 `cmp` 沒有輸出。並記錄上傳、處理與總耗時（本機 compose 的數字見 `docs/service.md`）。

### 4.3 處理中刪除 worker

Job 變成 `running` 後：

```shell
kubectl -n openmot delete pod -l app.kubernetes.io/name=worker
```

預期替代的 worker Ready 後 Job 變成 `succeeded`，Result 與 CLI 相同。記錄所花時間。

### 4.4 重建 api、worker 與 Redis 後仍可取回

Job `succeeded` 後：

```shell
kubectl -n openmot delete pod -l app.kubernetes.io/name=api
kubectl -n openmot delete pod -l app.kubernetes.io/name=worker
kubectl -n openmot delete pod -l app.kubernetes.io/name=redis
```

等 Pod 重新就緒後，預期 `GET /jobs/{job_id}` 仍是 `succeeded` 且 Result 可取回。

### 4.5 校正資源值

處理 MOT17 序列期間量測（k3s 預設附有 metrics-server）：

```shell
kubectl top pods -n openmot
```

目前 requests 與 limits 是未校準的暫定值（api 100m／256Mi、上限 1Gi；Redis 100m／128Mi、上限 512Mi；worker 2 CPU／6Gi、上限 12Gi），量到的數字填入下表後再調整清單：

| 工作負載 | CPU（量測） | 記憶體（量測） |
| --- | --- | --- |
| api | 未量測 | 未量測 |
| redis | 未量測 | 未量測 |
| worker | 未量測 | 未量測 |

### 叢集整合測試

上述 4.2 到 4.4 也有自動化的版本，會部署 overlay、提交 Job 並刪除 Pod。它只在明確設定 `KUBECONFIG`、叢集可連、節點有 GPU、而且叢集上**尚未有** `openmot` Namespace 時才會執行（結束時會刪除整個 Namespace），否則 skip 並說明原因：

```shell
KUBECONFIG=/etc/rancher/k3s/k3s.yaml python -m pytest -m integration tests/test_k8s_e2e.py -rs
```
