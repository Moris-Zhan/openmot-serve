# Proposal

## Why

`add-kubernetes-deployment` 交付了 Kubernetes 清單、渲染與 schema 檢查、部署文件與叢集整合測試，但當時沒有可用的 k3s 環境（沒有宿主機或獨立 VM，Docker daemon 上的特權 k3s 受節點 inotify 限制無法啟動），所有需要叢集才能觀察的行為都沒有驗證。這些行為（GPU 分配、探針的實際表現、Pod 重建後的復原、資源用量）是路線圖 v0.3 的核心，不能因為沒有環境就被當成已完成，也不該在沒有驗證的情況下成為主規格的規範。

## What Changes

- 取得一個可用的單節點 k3s 叢集（宿主機或獨立 VM，具備 NVIDIA 驅動、Container Toolkit 與裝置外掛），完成 GPU 評估。
- 依 `docs/kubernetes.md` 的外部叢集驗證手冊實際部署並驗證：冷部署、GPU 分配與無 GPU 時的行為、端對端結果與 CLI 逐位元組相同、處理中刪除 worker、重建 api、worker 與 Redis 後仍可取回。
- 在可用叢集上執行 `tests/test_k8s_e2e.py` 並記錄結果。
- 以 `kubectl top` 校準資源值並更新清單與文件。
- 把驗證結果寫回 `docs/kubernetes.md`，移除「未經實機驗證」的聲明，並更新 README 路線圖 v0.3 的勾選。
- 新增需要叢集才能成立的行為需求到 `kubernetes-deployment` 規格。
- 不包含：GPU Operator、Helm、多節點、Ingress、HPA、NetworkPolicy。

## Capabilities

### New Capabilities

（無。）

### Modified Capabilities
- `kubernetes-deployment`: 新增在真實叢集上才能觀察的行為需求（冷部署就緒、GPU 排程、狀態在 Pod 重建後保留、中斷的 Job 完成、探針的實際表現、worker 更新的安全性、無效設定的可見失敗）。

## Impact

- **前置條件**：使用者提供一個能安裝 k3s 並可存取 GPU 的環境（宿主機或獨立 VM），以及 kubeconfig；在此之前這個變更無法實作。
- **程式碼**：預期只調整清單的資源值與文件；若驗證發現清單或程式需要修正（例如 SIGTERM 的行為、`readOnlyRootFilesystem`），另行記錄。
- **文件**：`docs/kubernetes.md`、README。
