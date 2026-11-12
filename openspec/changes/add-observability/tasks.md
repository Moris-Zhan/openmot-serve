# Tasks

## 1. 指標定義、設定與指標伺服器（observability）

- [x] 1.1 先寫失敗的測試再實作 `metrics/definitions.py`：`create_metrics(registry)` 回傳不可變的 `Metrics`，含五個指標（`openmot_http_requests_total`、`openmot_http_request_duration_seconds`、`openmot_jobs_total`、`openmot_job_duration_seconds`、`openmot_frames_processed_total`）的名稱、型別、標籤與 bucket，佇列深度由 2.2 提供；把 `prometheus_client`（固定版本）加到 `pyproject.toml` 的 `service` 依賴與 `deploy/docker/api-requirements.txt`；驗證：測試涵蓋名稱與標籤、兩個獨立 registry 不互相污染、兩處依賴的版本一致；`ruff check .` 乾淨
- [x] 1.2 先寫失敗的測試再擴充 `jobs/settings.py`：新增 `OPENMOT_API_METRICS_PORT`（預設 9101）與 `OPENMOT_WORKER_METRICS_PORT`（預設 9102），新欄位放在 dataclass 尾端並有預設值，非整數或超出範圍時丟 `SettingsError` 並指出變數名；驗證：新測試通過，既有的 `test_settings.py` 不修改且全部通過
- [x] 1.3 先寫失敗的測試再實作 `metrics/server.py`：在指定埠啟動指標伺服器；埠已被占用時把原因寫到標準錯誤並回傳「未啟動」，不丟例外；驗證：測試在隨機埠啟動真實伺服器並以 HTTP 抓到 `200` 與 Prometheus 文字格式；占用同一個埠再啟動時只輸出錯誤、不丟例外
- [x] 1.4 新增 `docs/observability.md`（繁體中文）的「指標」章節：各指標的名稱、型別、標籤與含義，兩個指標埠與環境變數，說明指標不在對外 API 上；驗證：文件測試檢查章節存在，文件列出的指標名稱與 `create_metrics` 實際輸出的名稱一致

## 2. api 指標（observability）

- [x] 2.1 先寫失敗的測試再實作 `metrics/http.py` 的 middleware，並讓 `create_app` 接受選用參數 `metrics: Metrics | None = None`：以路由模板、方法、狀態碼記錄請求數與延遲，沒有匹配的路由歸為固定的 `unmatched`，實際 job ID 不出現在任何標籤；驗證：測試涵蓋規格的三個情境（模板路由、兩個未知路徑種類數不增加、`5xx` 狀態碼可見）與「對外埠 `GET /metrics` 回 `404`」；既有的 api 測試不修改且全部通過
- [x] 2.2 先寫失敗的測試再實作 `metrics/queue.py` 的佇列深度 collector：被抓取時以專用的 Redis 連線（連線與讀取逾時各 2 秒）執行 `LLEN`，佇列名稱取自 Celery 設定的預設佇列；連線失敗或逾時時不輸出此指標、其餘指標照常輸出、抓取不失敗；驗證：測試以假的 Redis 涵蓋「3 個等待中的工作時為 3」與「Redis 失敗時指標缺席」；佇列名稱由測試與 `create_celery` 的設定比對，不是寫死的字串
- [x] 2.3 先寫失敗的測試再修改 `app/run.py`：`main` 建立 registry 與 `Metrics`，把它們傳入 `create_app`，註冊佇列深度 collector，並以 `OPENMOT_API_METRICS_PORT` 啟動指標伺服器（可注入，測試不開真實埠）；驗證：測試確認組裝順序與埠設定，指標埠綁定失敗時服務仍然啟動；在程序內用真實指標伺服器與 `TestClient` 送請求後，抓取的輸出中請求計數增加

## 3. worker 指標（observability）

- [x] 3.1 先寫失敗的測試再修改 `worker/celery_app.py` 的 `register_task`：task 在呼叫 `process_job` 前數 `frames_dir` 的影格數並記下單調時鐘，結束後依 `ProcessOutcome` 記錄；`outcome` 計數一律加一，`succeeded` 與 `failed` 記錄耗時，只有 `succeeded` 增加影格計數，`skipped` 不記錄耗時與影格；時鐘與 registry 以參數注入；驗證：測試涵蓋規格的三個情境（成功 N 張影格、失敗、略過），並確認 `worker/processing.py` 沒有任何修改（`git diff` 為空）、既有的 worker 測試不修改且全部通過
- [x] 3.2 先寫失敗的測試再修改 `worker/run.py`：以 `OPENMOT_WORKER_METRICS_PORT` 啟動指標伺服器（可注入）；綁定失敗時 worker 仍然服務；驗證：測試確認組裝與降級行為；在程序內用真實指標伺服器模擬一次工作後，抓取的輸出含 `openmot_jobs_total{outcome="succeeded"}`
- [x] 3.3 更新 `docs/observability.md`：加上「worker 指標」與「單程序假設」說明，並如實寫明 worker 在沒有 GPU 的環境無法端到端驗證；驗證：文件測試檢查這些說法存在

## 4. 監控 overlay（observability）

- [x] 4.1 先寫失敗的測試再建立 `deploy/docker/docker-compose.monitoring.yml`、`monitoring/prometheus.Dockerfile`、`monitoring/grafana.Dockerfile`、`monitoring/prometheus.yml` 與 Grafana provisioning（資料來源與 dashboard 提供者）：基底映像固定版本標籤（實作時挑選並實際拉取確認），設定以 `COPY` 進映像、不使用 bind mount；Prometheus 與 Grafana 的對外埠綁 `127.0.0.1`，api 與 worker 的指標埠不發佈到主機；Prometheus 抓取 `api:9101` 與 `worker:9102`；Grafana 密碼用 `${GRAFANA_ADMIN_PASSWORD:?…}`；驗證：測試解析 overlay 與 Dockerfile 涵蓋規格的三個情境；有 Docker 時 `docker compose -f … -f … config` 在未設定密碼時失敗並指出變數名、設定後成功；既有的 `docker-compose.yml` 沒有任何修改且既有的 compose 測試全部通過
- [x] 4.2 更新 `docs/observability.md`：加上「啟動監控」章節（疊加指令、需要設定的環境變數、本機網址、只綁 `127.0.0.1` 與密碼的原因）；驗證：文件測試檢查列出的指令與 overlay 檔名一致

## 5. 警報規則與 CI 的 promtool（observability）

- [x] 5.1 先寫失敗的測試再建立 `monitoring/rules/openmot.rules.yml` 與 `monitoring/rules/openmot.rules.test.yml`：四條規則（`ApiDown`/`WorkerDown`、`HighJobFailureRatio`、`JobQueueBacklog`、`HighApiErrorRatio`），門檻與 `for` 依設計 D8；每條規則至少一個「觸發」與一個「不觸發」的測試案例（含「只有一個工作失敗不觸發」）；驗證：本機下載 promtool 並比對 SHA-256 後，`promtool check rules` 與 `promtool test rules` 通過；突變（把失敗率門檻改到永遠不成立、拿掉最小工作數條件）會使 `promtool test rules` 失敗
- [x] 5.2 先寫失敗的測試再建立 `tests/test_alert_rules.py`：以 pytest 呼叫 promtool 做規則檢查與單元測試，沒有 promtool 時 skip 並說明原因；另外以 pyyaml 檢查四條規則都存在、都有 `for` 與 `summary` 註解、規則引用的指標名稱都是服務會輸出的；驗證：有 promtool 時全部通過，沒有時 skip 訊息可讀
- [x] 5.3 先寫失敗的測試再修改 `.github/workflows/ci.yml` 的 `test` 工作：新增獨立的 promtool 安裝步驟（下載 Prometheus 發行檔、`sha256sum --check`、取出 `promtool`、版本檢查），不動既有的 kubectl、kustomize、kubeconform 步驟；工作流程測試檢查步驟存在、校驗碼為 64 位十六進位、版本與 `docs/ci.md` 一致；更新 `docs/ci.md`；驗證：`actionlint` 通過，`tests/test_ci_workflow.py` 與 `tests/test_ci_docs.py` 全部通過，突變（刪掉校驗步驟）會被測試抓到
- [x] 5.4 更新 `docs/observability.md`：加上「警報規則」章節（四條規則的條件表、門檻是未經真實流量校準的起始值、沒有 Alertmanager 與通知、沒有 worker 的堆疊上 `WorkerDown` 會觸發是預期行為）；驗證：文件測試檢查規則名稱與規則檔一致、起始值的聲明存在

## 6. Grafana dashboard（observability）

- [x] 6.1 先寫失敗的測試再建立 `monitoring/grafana/dashboards/openmot.json`：七個面板（請求率、API 延遲 p95、`5xx` 比例、工作結果、工作耗時 p50 與 p95、影格吞吐、佇列深度），並確保 provisioning 會載入它；驗證：測試解析 JSON，確認面板數量與每個面板至少一個查詢；從所有查詢抽出的 `openmot_` 指標名稱都出現在 `create_metrics` 在測試 registry 上實際輸出的名稱中；突變（把一個面板改成不存在的指標）會使測試失敗並指出面板與指標名稱
- [x] 6.2 更新 `docs/observability.md`：加上「dashboard」章節（七個面板的含義、如何開啟、沒有真實流量時面板會是空的是預期行為）；驗證：文件測試檢查面板清單與 dashboard JSON 的面板標題一致

## 7. 端到端驗證（observability）

- [x] 7.1 在本機實際疊加 overlay 並驗證（以標記 `integration` 的測試實作，沒有 Docker 時 skip 並說明原因）：啟動 Redis、api、Prometheus、Grafana（不啟動 worker），確認 Prometheus 的 api 目標為 `up`、送請求後請求指標增加、不啟動 worker 時送出一個工作後佇列深度為 1、Grafana 在沒有手動操作下找得到 dashboard 與資料來源、每個面板的查詢都能被 Prometheus 的查詢 API 解析；記錄實際結果；若側車環境的埠無法從測試程序連到，改以容器內的指令查詢並如實記錄；worker 指標的端到端無法驗證，如實記錄為限制
- [ ] 7.2 取得使用者同意後推送一個分支，用 `gh run watch` 確認 `test` 工作的 promtool 步驟與警報規則測試在真實 runner 上通過並記錄耗時；`gh` 未登入時改由使用者執行並貼回結果

## 8. README 與收尾（observability）

- [x] 8.1 更新 README：API 表的 `GET /metrics` 一列改為說明「指標在獨立埠，不屬於對外 API」；Status 反映 v0.4 進度並如實保留未驗證的部分（worker 指標無端到端、門檻未校準、Kubernetes 監控清單不在範圍內）；路線圖的 Prometheus 指標、Grafana dashboard 與警報規則三項依實際完成度勾選並註明限制；目錄圖的 `monitoring/` 移除「planned」；先寫失敗的測試檢查這些說法與實際檔案一致；驗證：README 測試通過
- [x] 8.2 執行預設的完整測試（不含 `gpu`、`data`、`integration`）、`ruff check .` 與 `openspec validate add-observability --strict`；確認 `docs/observability.md` 的限制章節與實際驗證結果相符，沒有把未驗證的行為說成已驗證；驗證：三者皆通過

## Workflow follow-up

- 全部完成後，用 `/opsx:archive` 封存這個變更並把 delta spec 同步到主 spec。
