# Design

## Context

動機見 proposal.md 的 Why，行為合約見 `kubernetes-deployment` spec。現況（已唯讀確認）：

- v0.2 的 compose 已定義三個服務與其行為：api 在 8000 埠、`/health` 檢查佇列與儲存；worker 啟動時先做環境檢查再載入偵測器（約 20 秒），失敗即非 0 結束；Redis 啟用 AOF；api 與 worker 共用 `/data/storage`；權重掛在 `/models/yolox_x.pth`。映像為 `openmot-api` 與 `openmot-worker`（約 0.76 GB 與 11.4 GB），都以 uid 1000 執行。設定全部來自 `OPENMOT_*` 環境變數。
- worker 使用 Celery solo pool：處理 Job 時主執行緒被占用，因此 Celery ping 在處理期間不會回應（v0.2 的驗證與疑難排解文件已記錄）。單一 Job 的時間上限預設 1500 秒，鎖逾時與訊息重新投遞等待時間為 1800 秒。
- 本機目前沒有 kubectl、kustomize、helm 或 k3s。開發環境是沒有特權的容器（無 systemd、`NoNewPrivs`），Docker daemon 在另一個容器，但可以啟動特權容器並看得到 RTX 5090 的裝置節點（已實測：`docker run --privileged --gpus all` 可用，cgroup v2）。尚未驗證 k3s 能否在其上執行並取得 GPU。
- 目標叢集是單節點 k3s，GPU 為 RTX 5090（CUDA 12.8 以上）。

## Goals / Non-Goals

**Goals:**
- 清單在沒有叢集的環境也能渲染與檢查，預設測試不需要叢集。
- 能取得叢集時做真實的端到端驗證，包含 GPU 排程與 Pod 刪除後的復原。
- 與 compose 部署共用同一組設定名稱與預設值，不重複定義行為。

**Non-Goals:**
- 不做多節點、ReadWriteMany、S3、Ingress、TLS、HPA、NetworkPolicy。
- 不管理 NVIDIA 裝置外掛或 GPU Operator 的安裝，只在文件中說明前置條件。
- 不修改 `app/`、`worker/`、`jobs/`、`tracking/` 的行為；若驗證發現需要，另外記錄再處理。

## Decisions

### D1：純 YAML 加 kustomize，一個 base 與一個 k3s overlay
`deploy/k8s/base/` 放與叢集無關的資源（namespace、ConfigMap、Redis、api、worker、PVC、Service）；`deploy/k8s/overlays/k3s-single-node/` 放單節點特有的內容（Service 改為 NodePort、儲存類別、權重在節點上的路徑、映像標籤）。設定值用 kustomize 的 `configMapGenerator`，內容變更會產生新雜湊並觸發滾動，避免改了設定卻沒重啟。
*替代方案*：Helm chart——多一層模板，測試與審查都較複雜，專案只有一個目標環境；GPU Operator 一併納入——範圍大且需要叢集才能驗證。

### D2：驗證分三層，環境不足時降級而不是跳過
1. **無叢集（預設測試）**：以 kustomize 渲染，再用 pyyaml 解析渲染結果並檢查 spec 的關鍵屬性（GPU 請求、探針、資源、非 root、唯讀權重掛載、單一 worker 與 Recreate、設定名稱與 compose 一致）。沒有 kustomize 時 skip 並說明原因，與 `test_compose_file.py` 的做法一致。`pyyaml` 加進 `dev` extras。
2. **有 k3s 但沒有 GPU**：驗證 api、Redis、PVC、探針、Service 與「沒有 GPU 時 worker 維持 Pending 而 api 不受影響」這個情境。
3. **有 k3s 與 GPU**：完整端到端，包含 worker Pod 刪除後的復原，結果與 CLI 逐位元組相同。
第 2、3 層標記為 `integration`，需要 `KUBECONFIG` 指向可連的叢集，否則 skip。

### D3：先做環境可行性評估，有明確的通過條件與降級路徑
可行性評估是第一個任務，不是前提假設。順序：(a) 使用者能提供宿主機或獨立 VM 與 kubeconfig；(b) 在 Docker daemon 上以特權容器執行 k3s（`rancher/k3s`），GPU 需要在映像中加入 NVIDIA Container Toolkit 並以 `--gpus all` 啟動；(c) 以上皆不可行時，停在第 1 層，完整保留清單，並在文件寫出在外部 k3s 叢集的部署與驗證指令。不在現有的開發容器內安裝 k3s。評估有時間上限，結果（含失敗原因）記錄在文件，tasks 依結果決定實機驗證的範圍，不把降級當成靜默跳過。
**評估結論（已定）**：沒有宿主機或獨立 VM；Docker daemon 與開發環境同屬一個 Pod，特權 k3s 因節點 inotify 限制無法啟動；既有叢集 `ai-model` 是共用的 GPU 開發叢集，不列為部署目標。因此本變更停在第 1 層：清單、渲染測試與 schema 檢查完成，實機驗證（第 2、3 層與 spec 中需要叢集的情境，如冷部署、GPU 分配、Pod 重建後的復原）**尚未驗證**，以 `docs/kubernetes.md` 的外部執行手冊交付，並由後續變更 `verify-kubernetes-on-cluster` 承接（主規格只收錄無叢集即可檢查的清單屬性）。
*替代方案*：kind——k3s 才是專案的目標環境，且 kind 的 GPU 支援更弱。

### D4：儲存用單一 local-path PVC，不加 podAffinity
api 與 worker 掛載同一個 ReadWriteOnce 的 PVC。k3s 的 local-path 供應器建立的 PV 帶有節點親和，兩個 Pod 因此必然落在同一節點，單節點下不需要另外的 podAffinity；多寫入者在同一節點上對 RWO 卷是允許的。額外加 `requiredDuringScheduling` 的 podAffinity 反而會在 api 不存在時讓 worker 卡在 Pending。Pod 設定 `fsGroup: 1000`，使 uid 1000 能寫入。Redis 用 StatefulSet 的 `volumeClaimTemplates` 取得自己的 PVC，啟用 AOF。
*替代方案*：RWX（NFS）——單節點 k3s 預設沒有；S3——列在路線圖的 Later。

### D5：探針依「重啟有沒有用」設計
- **api**：`readinessProbe` 用 `httpGet /health`（佇列或儲存壞時回 503，從 Service 端點移除）；`livenessProbe` 只用 TCP 檢查 8000 埠，因為 Redis 掛掉時重啟 api 沒有幫助，卻會製造重啟迴圈；`startupProbe` 給啟動寬限。
- **worker**：`startupProbe` 用 exec 執行 `python -m worker.healthcheck`，週期與失敗次數足以涵蓋載入偵測器（約 20 秒，給數倍餘裕）；`readinessProbe` 同一指令，處理長 Job 時可能暫時 NotReady，但 worker 不在任何 Service 後面，不影響流量；**不設 livenessProbe**，因為 solo pool 忙碌時無法與卡住區分，進程結束時 kubelet 本來就會依 `restartPolicy` 重啟。
- **Redis**：`redis-cli ping` 作為 readiness 與 liveness。

### D6：worker 單副本、Recreate、優雅終止涵蓋時間上限
單一 GPU 下，滾動更新會讓新舊 worker 同時存在而搶卡，所以 `strategy: Recreate`、`replicas: 1`。`terminationGracePeriodSeconds` 設為任務時間上限加緩衝（預設 1500 加 60），讓 SIGTERM 的暖關閉（Celery 等目前任務結束）有機會完成；超時被強制終止時，沿用 v0.2 的復原機制（`acks_late` 加鎖逾時後重新投遞）。

### D7：GPU 使用方式
worker 容器宣告 `resources.limits["nvidia.com/gpu"]: 1`，並指定 `runtimeClassName: nvidia`。前置條件（寫入文件，不在 repo 內管理）：節點已安裝 NVIDIA 驅動與 Container Toolkit、k3s 偵測到 `nvidia` runtime、叢集已有 NVIDIA 裝置外掛。沒有可分配的 GPU 時，Pod 因排程失敗維持 Pending，事件會指出 `Insufficient nvidia.com/gpu`。

### D8：映像與權重的取得
映像不上傳任何 registry：以 `docker save` 加上 k3s 的 `ctr images import`（或叢集節點上的 `docker`/containerd）載入，清單用 `imagePullPolicy: IfNotPresent` 與 kustomize 的 `images:` 轉換器指定標籤。權重以唯讀的 `hostPath`（`type: File`）掛入 worker，路徑由 overlay 指定，不進映像也不進清單內容；單節點下 hostPath 是最簡單且不需要額外工具的作法。
*替代方案*：把權重放進 PVC 並用 Job 填入——多一個一次性資源，單節點下沒有額外好處；registry——需要另外維護一個服務。

### D9：安全預設
所有 Pod 設定 `runAsNonRoot`、`runAsUser: 1000`、`allowPrivilegeEscalation: false`、捨棄所有 capabilities、`seccompProfile: RuntimeDefault`。`readOnlyRootFilesystem` 預設不開：沒有實機就無法確認 torch 與 Celery 是否需要寫入其他目錄（需補 `/tmp` 等 emptyDir），文件列為實機驗證後再收緊的建議；測試只守住不是 root 與非特權。

### D10：資源值先暫定，用實測校正
初始值：api 請求 100m CPU 與 256Mi 記憶體、記憶體上限 1Gi；Redis 請求 100m 與 128Mi、上限 512Mi；worker 請求 2 CPU 與 6Gi、記憶體上限 12Gi。這些是暫定值，實機驗證時以 `kubectl top` 與實際處理 MOT17 序列的觀測校正，並把量到的數字寫進文件；沒有叢集可量測時保持暫定並在文件標明（目前即是此情況：值為未校準的暫定值）。

## Risks / Trade-offs

- [k3s 在 Docker daemon 上以特權容器執行，GPU 直通不一定成立] → D3 的降級路徑；至少完成第 2 層驗證與靜態驗證，並提供外部叢集的完整指令。
- [映像要先進叢集的 containerd，容易漏] → 文件與驗證腳本明確包含載入步驟，`ErrImagePull` 列入疑難排解。
- [worker 忙碌時 readiness 變 NotReady，`kubectl rollout status` 可能被誤導] → 文件說明 worker 的 NotReady 在忙碌時是預期的，判斷依據是 Pod 未重啟與 Job 狀態。
- [`kubectl delete -k` 會連同 PVC 一起刪除，丟失全部 Job 資料] → 文件區分「移除工作負載」與「連資料一起移除」，不把整體刪除當成預設的復原手段。
- [hostPath 權重路徑綁定節點] → 單節點為設計前提，文件註明多節點時需改為其他方式。
- [local-path 儲存沒有備份] → 與 v0.2 一致，不在範圍內；文件註明。
- [資源值是暫定的] → D10。

## Migration Plan

部署：載入映像 → 放置權重 → 確認 GPU 裝置外掛 → `kubectl apply -k deploy/k8s/overlays/k3s-single-node` → 等待 Ready → 呼叫 `/health`。回滾：還原先前的映像標籤並重新套用；因為 worker 是 Recreate，回滾期間會有短暫沒有 worker 的時間，已排入佇列的 Job 會在新 worker Ready 後被處理。資料（兩個 PVC）不隨工作負載的更新而變動。
