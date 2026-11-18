# 單機 k3s 測試：無 GPU 階段（api／Redis）操作步驟

> 日期：2026-11-18｜狀態：待執行
>
> 這份文件是 `k3s_single_node_v03_v04_test_plan.md` 的**第一階段執行手冊**：新機器的 GPU 正在跑其他程序，所以先只驗證 k3s 本身與 api、Redis。它是操作步驟，不是測試報告；執行結果請記到計畫檔第 7 節的模板。
>
> 命令的來源是 `docs/kubernetes.md`、`deploy/k8s/` 的實際清單與 `docs/service.md`。除了 k3s 與 NVIDIA 工具的安裝（依官方文件），其餘都已對照儲存庫內容。

## 1. 先決條件與限制

- 新機器：Ubuntu 原生（有 systemd）、有 sudo、有 NVIDIA GPU（目前被其他程序占用，本階段不使用）。
- **worker 在程式裡寫死用 CUDA**（`worker/startup.py` 的 `WORKER_DEVICE = "cuda"`），而且儲存庫沒有 CPU-only overlay。所以本階段 worker 無法執行，會停在 `Pending`；這是預期行為，不是錯誤。
- 本階段**不安裝 NVIDIA Device Plugin**，也不建 worker 映像、不放權重檔。NVIDIA Container Toolkit 照常安裝，GPU 空出來後可以直接接續。

### 能測與不能測

| 能測（不需要 GPU） | 不能測（BLOCKED，等 GPU 空出來） |
|---|---|
| k3s 單節點啟動、Node Ready、DNS、`local-path` 儲存 | worker 啟動、載入偵測器 |
| api／Redis 部署、PVC Bound、NodePort、`/health` 回 200 | Job 處理到 `succeeded` |
| api 與 Redis 的 Pod 重建、探針行為 | worker 執行中被砍後 Job 重新投遞 |
| Redis 持久化：送出 Job 後重建 Redis Pod，Job 仍在 `queued` | GPU 排程、CUDA、`nvidia.com/gpu` |
| metrics Service 與（選做）監控清單 | worker 的 metrics |

沒有 GPU 時 worker 會停在 `Pending`，事件為 `Insufficient nvidia.com/gpu`，這同時完成計畫檔 T14（GPU 資源不足的負向測試）的一部分觀察。

## 2. Phase 0：clone 與主機盤點

```bash
git clone https://github.com/Moris-Zhan/openmot-serve.git
cd openmot-serve

uname -a
cat /etc/os-release | head -2
free -h
df -h /
sysctl fs.inotify.max_user_instances fs.inotify.max_user_watches
nvidia-smi
systemctl is-system-running
```

**Gate 0**：`systemctl is-system-running` 是 `running` 或 `degraded`（不是無法連線），磁碟至少有數十 GB 的空間。

## 3. Phase 1：Docker、NVIDIA Container Toolkit、k3s

```bash
# 依官方文件安裝 Docker 與 NVIDIA Container Toolkit（先於 k3s）
nvidia-container-runtime --version

# 安裝 k3s（單節點）
curl -sfL https://get.k3s.io | sh -

export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
sudo chmod 644 /etc/rancher/k3s/k3s.yaml     # 讓非 root 的 kubectl 可讀；不想放寬權限就一律用 sudo k3s kubectl
kubectl get nodes -o wide
kubectl get pods -A
kubectl get storageclass
```

**Gate 1**：Node 是 `Ready`，`kube-system` 的 CoreDNS、local-path-provisioner 等 Pod 是 `Running`，`local-path` StorageClass 存在。沒通過就停止，不要往下做。

若啟動失敗，先取證再處理：

```bash
sudo systemctl status k3s --no-pager
sudo journalctl -u k3s -n 100 --no-pager
```

遇到 `too many open files`，是 inotify 上限太低；修改 `sysctl` 前先確認你在正確的宿主機上。

## 4. Phase 2：建 api 映像並匯入 k3s

k3s 用自己的 containerd，不連任何 registry，清單使用 `imagePullPolicy: IfNotPresent` 和本機標籤 `dev`。本階段只需要 api 映像；Redis 用公開的 `redis:7-alpine`，k3s 會自己拉。

```bash
docker compose -f deploy/docker/docker-compose.yml build api
docker images | grep openmot-api          # 標籤應是 dev
docker save openmot-api:dev | sudo k3s ctr images import -
sudo k3s crictl images | grep openmot
```

**Gate 2**：`crictl images` 看得到 `openmot-api`，標籤 `dev`。看不到的話，Pod 會停在 `ErrImagePull` 或 `ImagePullBackOff`。

## 5. Phase 3：部署 api、Redis（worker 預期 Pending）

```bash
kubectl apply -k deploy/k8s/overlays/k3s-single-node

kubectl -n openmot rollout status statefulset/redis
kubectl -n openmot wait --for=condition=available deployment/api --timeout=300s

kubectl -n openmot get pods,svc,pvc -o wide
kubectl -n openmot get events --sort-by=.lastTimestamp
```

查看 worker 為什麼 Pending（預期是 `Insufficient nvidia.com/gpu`）：

```bash
kubectl -n openmot describe pod -l app.kubernetes.io/name=worker | tail -15
```

`/health`（`<節點IP>` 換成這台機器的位址；就在本機測時可用 `127.0.0.1`）：

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://<節點IP>:30080/health
```

**Gate 3**：

| 項目 | 預期 |
|---|---|
| `redis-0` | `1/1 Running` |
| `api` | `1/1 Running`，`deployment/api` 為 available |
| PVC `openmot-storage` 與 Redis 的 PVC | 都是 `Bound` |
| `worker` | `Pending`，事件是 `Insufficient nvidia.com/gpu` |
| `/health` | `200` |

## 6. Phase 4：Pod 重建與持久化

### 6.1 準備一個測試用 zip

```bash
python3 -c "
import cv2, numpy as np
from pathlib import Path
folder = Path('/tmp/openmot_demo/frames')
folder.mkdir(parents=True, exist_ok=True)
for number in range(1, 11):
    cv2.imwrite(str(folder / f'{number:06d}.jpg'), np.full((540, 960, 3), 128, np.uint8))
"
cd /tmp/openmot_demo/frames && python3 -m zipfile -c ../frames.zip *.jpg
```

新機器沒有 `cv2` 時，改用 `pip install opencv-python-headless numpy`，或用任何 10 張連續編號的 jpg（檔名 `000001.jpg` 起，直接放在 zip 最上層，不能有子資料夾）。

### 6.2 Redis 持久化（對應計畫檔 T17／TC-11）

```bash
JOB_ID=$(curl -s -X POST -F "file=@/tmp/openmot_demo/frames.zip" http://<節點IP>:30080/jobs \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['job_id'])")
echo $JOB_ID
curl -s http://<節點IP>:30080/jobs/$JOB_ID           # 狀態應為 queued（沒有 worker 處理）

kubectl -n openmot delete pod redis-0
kubectl -n openmot rollout status statefulset/redis

curl -s http://<節點IP>:30080/jobs/$JOB_ID           # 重建後 Job 還在，仍是 queued
```

**通過條件**：Redis Pod 重建後，同一個 `job_id` 仍查得到，狀態仍是 `queued`。Redis 是 StatefulSet 加 AOF，所以不是只有 PVC Bound 就算數。

### 6.3 api Pod 重建（T15／TC-09 的 api 部分）

```bash
kubectl -n openmot delete pod -l app.kubernetes.io/name=api
kubectl -n openmot rollout status deployment/api
curl -s -o /dev/null -w '%{http_code}\n' http://<節點IP>:30080/health   # 200
curl -s http://<節點IP>:30080/jobs/$JOB_ID                               # Job 仍在
```

### 6.4 探針行為（T16／TC-10）

api 的探針：startup 與 liveness 是 TCP 8000，readiness 是 HTTP `/health`。`/health` 在 Redis 或儲存不可用時回 `503`，所以可以這樣測 readiness：

```bash
kubectl -n openmot scale statefulset/redis --replicas=0
sleep 40
kubectl -n openmot get pods -l app.kubernetes.io/name=api       # api 變成 0/1（NotReady），但不重啟
kubectl -n openmot get endpoints api                            # 沒有 ready 的位址
curl -s -w '\n%{http_code}\n' http://<節點IP>:30080/health      # 503 或連線被拒（沒有 Ready 的 endpoint）

kubectl -n openmot scale statefulset/redis --replicas=1
kubectl -n openmot rollout status statefulset/redis
kubectl -n openmot get pods -l app.kubernetes.io/name=api       # 回到 1/1
```

**通過條件**：Redis 停掉時 api 變 NotReady 且 `RESTARTS` 不增加；Redis 回來後 api 恢復 Ready。手動刪 Pod 不能當作 liveness 的驗收，liveness 失敗要另外刻意製造。

## 7. Phase 5：監控（選做）

需要先安裝 Prometheus Operator（例如 `kube-prometheus-stack`），本專案不安裝它，儲存庫也沒有 Helm values。沒有 Operator 時，下面的 `kubectl apply -k` 會因為缺少 CRD 而失敗，這是預期的。

只做 metrics Service 的檢查（不需要 Operator）：

```bash
kubectl -n openmot get svc api-metrics worker-metrics --show-labels
kubectl -n openmot port-forward svc/api-metrics 19101:9101
# 另開終端機：
curl -fsS http://127.0.0.1:19101/metrics | head
```

裝好 Operator 之後：

```bash
kubectl get crd servicemonitors.monitoring.coreos.com prometheusrules.monitoring.coreos.com
kubectl apply -k deploy/k8s/monitoring
kubectl get servicemonitor,prometheusrule -n monitoring
kubectl get prometheus -A -o yaml        # 核對 serviceMonitorSelector 是否接受 release: monitoring
```

`release: monitoring` 是**未驗證的假設**；release 名稱不同時，依 `docs/kubernetes.md` 的「`release: monitoring` 的假設與覆寫」處理。這一節的完整案例是計畫檔第 11 節（M-01 到 M-12、MT-01 到 MT-08）。

## 8. 結果要怎麼記與怎麼宣稱

每個案例都記錄：日期、OS／核心、k3s 版本、manifest commit、實際指令、輸出、結論（PASS／FAIL／BLOCKED／N/A）。模板在計畫檔第 7 節。

| 案例 | 本階段預期 |
|---|---|
| TC-01 k3s 單機啟動 | 可測 |
| TC-02 api／Redis 啟動 | 可測（worker 為 Pending，另註明） |
| TC-03 Service／Job 端到端 | **BLOCKED**（沒有 worker） |
| TC-04 PVC 綁定 | 可測 |
| TC-05～TC-07 GPU 資源、CUDA、worker 推論 | **BLOCKED** |
| TC-08 第二 GPU Pod 資源不足 | 可部分觀察（worker 因資源不足 Pending） |
| TC-09 刪除 Pod 補建 | 可測 api 與 Redis，worker 部分 BLOCKED |
| TC-10 探針 | 可測 api |
| TC-11 Redis 重建 | 可測 |
| TC-12 worker 中斷後任務一致性 | **BLOCKED** |

**宣稱規則**（依計畫檔第 8 節）：沒有 GPU 時，只能宣告「單節點 api／Redis 部署與 Pod 重建已驗證」。README 的下面兩項**不可勾選**：

- `GPU scheduling for the worker`
- `Verify the deployment, GPU scheduling and pod recovery on a live k3s cluster`

`BLOCKED` 不能視為 `PASS`。

## 9. 清理（需要時）

```bash
# 只移除工作負載，PVC 與資料保留；再次 apply 即可恢復
kubectl -n openmot delete deployment/api deployment/worker

# 連 Namespace、PVC 與資料一起移除，無法復原；只在確定不需要資料時使用
kubectl delete -k deploy/k8s/overlays/k3s-single-node
```

## 10. GPU 空出來之後的接續步驟

1. 補裝 NVIDIA Device Plugin（依官方 README；k3s 上它的 DaemonSet 需要 `runtimeClassName: nvidia`），並建立名為 `nvidia` 的 RuntimeClass（`handler: nvidia`）。
2. 確認 `kubectl describe node | grep nvidia.com/gpu` 顯示 `Capacity` 與 `Allocatable` 都有 1，跑一次 CUDA smoke Pod（計畫檔第 5 節 Phase 3）。
3. 建 worker 映像（約 11.4 GB）並匯入 k3s：
   ```bash
   docker compose -f deploy/docker/docker-compose.yml build worker
   docker save openmot-worker:dev | sudo k3s ctr images import -
   ```
4. 放權重（SHA-256 必須是 `5652330b6ae860043f091b8f550a60c10e1129f416edfdb65c259be6caf355cf`）：
   ```bash
   sudo install -D -m 0644 models/yolox_x.pth /var/lib/openmot/models/yolox_x.pth
   sha256sum /var/lib/openmot/models/yolox_x.pth
   ```
5. worker 會自動從 `Pending` 排程（若沒有，`kubectl -n openmot rollout restart deployment/worker`），然後：
   ```bash
   kubectl -n openmot wait --for=condition=available deployment/worker --timeout=600s
   ```
6. 重做 TC-03、TC-05 到 TC-08、TC-12，通過後才能依計畫檔第 8 節勾選 README 的兩個 v0.3 項目。
