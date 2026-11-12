# Proposal

## Why

服務已能接收 Tracking Job、由 GPU worker 處理並回傳結果，CI 也會在每次 push 檢查回歸，但執行中的服務沒有任何可觀測的訊號：請求變慢、工作失敗率升高、佇列積壓，都只能靠使用者回報或手動翻日誌才知道。路線圖 v0.4 的後半就是 Prometheus 指標、Grafana dashboard 與警報規則；README 的 API 表也已預告 `/metrics`。CI 已完成，現在加上指標才有回歸保護。

## What Changes

- 新增 `metrics/` 套件：可注入的 registry 與指標定義，不使用 `prometheus_client` 的全域預設 registry，測試可各用各的。
- **api**：以 middleware 記錄請求延遲與請求數（路由用模板，如 `/jobs/{job_id}`，不含實際 job ID）；指標在**獨立埠**提供，不經由對外的 API 埠。同一個埠也提供佇列深度（讀 Redis 的 Celery 佇列長度；Redis 連不上時不輸出，而不是輸出 0）。
- **worker**：在 task 包裝層依 `ProcessOutcome` 記錄工作總數（`succeeded`、`failed`、`skipped`）、處理耗時（`skipped` 不計入）、處理影格數；以 `prometheus_client` 在獨立埠提供指標。指標埠無法綁定時記錄錯誤並繼續處理工作，不讓輔助功能拖垮主功能。
- 新增 `prometheus_client`（固定版本）到 `service` 依賴與 api 映像的相依。
- 新增 monitoring overlay `deploy/docker/docker-compose.monitoring.yml`（Prometheus 加 Grafana），設定放在 `monitoring/`：`prometheus.yml`、警報規則、Grafana provisioning 與一張 dashboard（七個面板）。映像固定到明確版本。Prometheus 與 Grafana 只綁 `127.0.0.1`；指標埠不對主機發佈；Grafana 管理員密碼取自環境變數，未設定就拒絕啟動。
- 新增四條警報規則（`ApiDown`/`WorkerDown`、`HighJobFailureRatio`、`JobQueueBacklog`、`HighApiErrorRatio`），每條以 `promtool test rules` 做單元測試；CI 的 `test` 工作安裝固定版本並比對 SHA-256 的 `promtool`，本機沒有 promtool 時相關測試 skip 並說明原因。門檻是未經真實流量校準的起始值。
- 新增 `docs/observability.md`（繁體中文）；更新 README（API 表的 `/metrics` 說明、Status、路線圖、目錄圖）與 `docs/ci.md`。
- 不包含：Kubernetes 的監控清單（ServiceMonitor、monitoring namespace）、GPU 指標（dcgm-exporter）、Alertmanager 與任何通知管道、排隊等待時間指標、追蹤（tracing）與日誌聚合、真實流量下的門檻校準。

## Capabilities

### New Capabilities
- `observability`: api 與 worker 以獨立埠提供 Prometheus 指標（請求延遲、工作結果與耗時、影格吞吐、佇列深度）；以一個 compose overlay 啟動 Prometheus 與 Grafana，dashboard 自動載入；警報規則可被單元測試驗證；監控元件預設只對本機開放。

### Modified Capabilities

（無。`tracking-job-api` 的既有需求不變；指標埠不屬於對外 API，所以 README 的 `/metrics` 一列改為說明獨立埠，而不是新增 API 需求。）

## Impact

- **新增檔案**：`metrics/`、`deploy/docker/docker-compose.monitoring.yml`、`monitoring/`（Prometheus 設定、規則與測試、Grafana provisioning 與 dashboard）、`docs/observability.md`，以及對應測試。
- **修改檔案**：`app/api.py` 或 `app/run.py`（middleware 與指標伺服器）、`worker/celery_app.py` 與 `worker/run.py`（記錄與指標伺服器）、`jobs/settings.py`（指標埠設定）、`pyproject.toml` 與 `deploy/docker/api-requirements.txt`（依賴）、`.github/workflows/ci.yml`（promtool）、`README.md`、`docs/ci.md`。
- **外部系統**：Prometheus 與 Grafana 映像（Docker Hub）；CI 下載 promtool 發行檔。第一次推送到 `main` 時 `publish` 會再次發佈映像，屬於 CI 既有行為。
- **風險**：本開發環境沒有 GPU，worker 依規格不會啟動，所以 worker 指標只能以程序內的真實 HTTP 伺服器測試驗證，無法做端到端；警報門檻與 dashboard 數值沒有真實流量可校準，文件須如實標註。
