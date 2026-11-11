# Tasks

## 1. 環境可行性評估（kubernetes-deployment）

- [x] 1.1 在開發環境（不在映像內）安裝 kubectl、kustomize、kubeconform 的固定版本到使用者目錄，驗證三者的 `version` 指令都能執行，並記錄版本與下載來源
- [x] 1.2 評估能否取得可安裝 k3s 的環境（結論：不可取得）：使用者沒有宿主機或獨立 VM；Docker daemon 上的特權 k3s 因節點 inotify 限制（`too many open files`）無法啟動，且既有叢集 `ai-model` 不列為部署目標；失敗原因與所用指令已記錄，驗證層級降為「無叢集」
- [ ] 1.3 （待外部叢集，目前無 k3s 環境，不執行）評估 GPU：在 k3s 節點安裝 NVIDIA Container Toolkit 設定與裝置外掛，驗證 `kubectl describe node` 顯示 `nvidia.com/gpu: 1`，且一個測試 Pod 內 `nvidia-smi` 看得到 RTX 5090；失敗時記錄原因並改走「無 GPU」路徑，不再嘗試其他安裝方式
- [ ] 1.4 在 `docs/kubernetes.md`（繁體中文）建立「驗證環境」一節，寫入 1.1、1.2 的結果與選定的驗證層級（無叢集），並明寫「未經實機驗證」；驗證文件中可執行的指令照字面執行得到相同結果

## 2. 清單 base（kubernetes-deployment）

- [x] 2.1 先寫失敗的測試再建立骨架：`pyyaml` 加入 `dev` extras、`deploy/k8s/base/kustomization.yaml` 與以 `configMapGenerator` 產生的設定（base 不寫死 namespace，Namespace 資源由 3.1 的 overlay 提供）；測試渲染 base 並檢查設定的變數名稱與預設值與 compose 一致；沒有 kustomize 時 skip 並說明原因；驗證測試通過且突變（改一個變數名稱）會失敗
- [x] 2.2 加入共用儲存的 PVC 與 Redis 的 StatefulSet（AOF、`volumeClaimTemplates`、`redis-cli ping` 探針）與 Service；先寫測試檢查 AOF 指令、PVC 存取模式與探針；驗證測試通過
- [x] 2.3 加入 api 的 Deployment 與 ClusterIP Service：readiness 用 `/health`、liveness 只做 TCP、startupProbe、資源 requests 與 limits、非 root 與 `fsGroup`、掛載共用 PVC；先寫測試檢查這些屬性；驗證測試通過且突變（liveness 改成 `/health`）會失敗
- [x] 2.4 加入 worker 的 Deployment：`nvidia.com/gpu: 1` 與 `runtimeClassName: nvidia`、`replicas: 1` 與 `Recreate`、startup 與 readiness 的 exec 探針且沒有 livenessProbe、優雅終止時間大於任務時間上限、唯讀的權重 hostPath、資源與安全設定；先寫測試檢查；驗證測試通過且突變（移除 GPU 請求、加上 liveness、策略改 RollingUpdate）都會失敗
- [x] 2.5 加入跨資源的測試：渲染結果沒有 root 或 privileged 容器、權重掛載唯讀、清單中沒有憑證類內容、所有工作負載都有探針與資源宣告；以 kubeconform 對渲染結果做 schema 檢查（沒有時 skip）；驗證測試通過

## 3. k3s 單節點 overlay（kubernetes-deployment）

- [x] 3.1 建立 `deploy/k8s/overlays/k3s-single-node/`：Namespace `openmot`、Service 改 NodePort 30080、儲存類別、權重在節點上的路徑、映像名稱與標籤；先寫測試檢查渲染結果；驗證 `kustomize build` 成功且測試通過
- [x] 3.2 撰寫映像載入的指令（`docker save` 匯入 k3s 的 containerd）與權重放置的指令，放進 `docs/kubernetes.md` 的「部署」一節；驗證文件中的指令照字面執行能讓映像出現在叢集中（沒有叢集時，標明需要在 4.1 驗證）

## 4. 實機驗證（kubernetes-deployment）

以下 4.1 到 4.5 需要外部 k3s 叢集，目前沒有環境，**待外部叢集執行**：在 `docs/kubernetes.md` 寫成完整的執行手冊（指令與預期輸出），不勾選；4.6 的測試可在無叢集下完成並驗證 skip。

- [ ] 4.1 （待外部叢集）依 1.4 選定的層級執行：有 k3s 時 `kubectl apply -k` overlay，驗證 api 與 Redis 變 Ready、經 Service 呼叫 `/health` 回 `200`；有 GPU 時另驗證 worker 取得 GPU 並 Ready；沒有 GPU 時改驗證 worker 維持 Pending、事件為 `Insufficient nvidia.com/gpu`、api 與 Redis 不受影響；沒有 k3s 時，改在文件寫出完整的外部叢集部署與驗證指令並標示未實測
- [ ] 4.2 （需要 GPU 與 k3s）提交 MOT17-09 的影格 zip、輪詢到 `succeeded`，驗證 Result 與 `python -m tracking.run` 的輸出逐位元組相同，並記錄各階段耗時；不具備條件時在文件標明未驗證
- [ ] 4.3 （需要 GPU 與 k3s）Job 處於 `running` 時刪除 worker Pod，驗證替代 Pod Ready 後 Job 進入 `succeeded`、Result 與 CLI 相同，並記錄所花時間；不具備條件時在文件標明未驗證
- [ ] 4.4 （需要 k3s）Job `succeeded` 後刪除 api 與 worker 的 Pod，再刪除 Redis 的 Pod，驗證 Job Status 與 Result 仍可取得；無法完成 worker 部分時，至少以 api 與 Redis 驗證 Job Status 的保留
- [ ] 4.5 （待外部叢集）有叢集時用 `kubectl top` 與實際處理的觀測校正 D10 的資源值，並在文件記錄量到的數字；沒有叢集時保留暫定值並在文件標明
- [ ] 4.6 以 `integration` 標記寫入叢集測試（提交、輪詢、取回、刪除 Pod 後復原），沒有 `KUBECONFIG`、叢集不可連或沒有 GPU 時 skip 並說明原因；在可用的叢集上執行並驗證通過，無叢集時驗證 skip 的訊息

## 5. 文件與收尾（kubernetes-deployment）

- [ ] 5.1 完成 `docs/kubernetes.md`：前置條件（驅動、Container Toolkit、裝置外掛、k3s）、映像與權重、`kubectl apply -k`、設定值、儲存與資料保存、移除工作負載與連資料一起移除的差別、疑難排解（`ErrImagePull`、`Insufficient nvidia.com/gpu`、worker 忙碌時 NotReady、PVC 權限）；驗證文件中的指令照字面執行（或標明未能實測的部分）
- [ ] 5.2 更新 README：勾選路線圖 v0.3 中已完成的項目並如實標示未完成者、Status 改成反映 v0.3、把部署架構章節的 _planned_ 標記對應到實際範圍（GPU Operator 不在範圍內，需如實寫明）；驗證 README 的說法與清單一致
- [ ] 5.3 執行預設的完整測試（不含 `gpu`、`data`、`integration`）與 `openspec validate add-kubernetes-deployment --strict`；驗證兩者皆通過

## Workflow follow-up

- 全部完成後，用 `/opsx:archive` 封存這個變更並把 delta spec 同步到主 spec。
