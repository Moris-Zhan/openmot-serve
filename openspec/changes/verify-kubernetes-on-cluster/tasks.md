# Tasks

## 1. 取得驗證環境（kubernetes-deployment）

- [ ] 1.1 由使用者提供宿主機或獨立 VM 與 kubeconfig，在其上安裝 k3s；驗證 `kubectl get nodes` 回傳 `Ready`，並確認 `KUBECONFIG` 指向的是這個叢集而不是其他叢集
- [ ] 1.2 評估 GPU：在 k3s 節點安裝 NVIDIA Container Toolkit 設定與裝置外掛，驗證 `kubectl describe node` 顯示 `nvidia.com/gpu: 1`，且一個測試 Pod 內 `nvidia-smi` 看得到 RTX 5090；失敗時記錄原因並改走「無 GPU」路徑，不再嘗試其他安裝方式

## 2. 實機驗證（kubernetes-deployment）

- [ ] 2.1 依 `docs/kubernetes.md` 的手冊冷部署：`kubectl apply -k` overlay，驗證 api 與 Redis 變 Ready、經 Service 呼叫 `/health` 回 `200`；有 GPU 時另驗證 worker 取得 GPU 並 Ready；另在沒有 GPU 的情境驗證 worker 維持 Pending、事件為 `Insufficient nvidia.com/gpu`、api 與 Redis 不受影響
- [ ] 2.2 提交 MOT17-09 的影格 zip、輪詢到 `succeeded`，驗證 Result 與 `python -m tracking.run` 的輸出逐位元組相同，並記錄各階段耗時
- [ ] 2.3 Job 處於 `running` 時刪除 worker Pod，驗證替代 Pod Ready 後 Job 進入 `succeeded`、Result 與 CLI 相同，並記錄所花時間；同時驗證載入偵測器期間不被重啟、處理長 Job 時不被終止
- [ ] 2.4 Job `succeeded` 後刪除 api 與 worker 的 Pod，再刪除 Redis 的 Pod，驗證 Job Status 與 Result 仍可取得；並驗證 Redis 不可用時 api 變 NotReady、`/health` 回 `503` 且 api 不被重啟
- [ ] 2.5 驗證更新 worker 映像時舊 Pod 先結束、新 Pod 才取得 GPU，以及無效的 `OPENMOT_PRECISION` 使 worker 啟動失敗且日誌指出該變數
- [ ] 2.6 用 `kubectl top` 與實際處理的觀測校正資源值，更新清單與文件並記錄量到的數字；同時評估 `readOnlyRootFilesystem` 是否可開啟
- [ ] 2.7 在可用的叢集上執行 `KUBECONFIG=... pytest -m integration tests/test_k8s_e2e.py`，驗證三項測試通過並記錄結果

## 3. 文件與收尾（kubernetes-deployment）

- [ ] 3.1 把驗證結果與耗時寫回 `docs/kubernetes.md`，移除「未經實機驗證」的聲明，並更新「驗證環境」一節的層級；驗證文件中的指令照字面執行得到相同結果
- [ ] 3.2 更新 README 路線圖 v0.3：勾選已驗證的項目、移除「not yet verified」的聲明；驗證 README 與測試（`tests/test_readme_k8s.py`）一致
- [ ] 3.3 執行預設的完整測試與 `openspec validate verify-kubernetes-on-cluster --strict`；驗證兩者皆通過

## 4. 監控的實機驗證（observability）

前置：`add-kubernetes-monitoring-manifests` 已完成並封存，且叢集已安裝 Prometheus Operator（含 `servicemonitors.monitoring.coreos.com`）與 Prometheus。

- [ ] 4.1 套用 `deploy/k8s/monitoring/`，驗證 `ServiceMonitor` 與 `PrometheusRule` 被建立、Prometheus 的 Targets 出現 api 與 worker 且 `up == 1`、時間序列帶有 `job="openmot-api"` 與 `job="openmot-worker"`；記錄 Operator 與 chart 的版本以及 `serviceMonitorSelector` 的設定
- [ ] 4.2 送一個 MOT17-09 的 Job，驗證 api 的請求延遲與吞吐、worker 的 Job 耗時與失敗計數、佇列深度都有更新（沒有 GPU 時 worker 的指標標為 BLOCKED，不得當成通過）
- [ ] 4.3 驗證 `ApiDown` 與 `WorkerDown` 規則已載入；刪除 api Pod 或讓 worker 停止，驗證 target 的 `up` 變成 0、告警進入 pending 再 firing，Pod 恢復後回到 `up == 1`；記錄各階段所花時間
- [ ] 4.4 把驗證步驟、結果與單機限制寫進 `docs/kubernetes.md`，並更新 README 路線圖 v0.4 的註記（由「未在叢集實測」改為已驗證的範圍）

## Workflow follow-up

- 全部完成後，用 `/opsx:archive` 封存這個變更並把 delta spec 同步到主 spec。
