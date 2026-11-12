# Design

## Context

動機見 proposal.md。目前的事實：

- api 由 `app/run.py` 以單一 uvicorn 程序執行，容器內監聽 `0.0.0.0:8000`，compose 把它發佈到主機的所有介面。`create_app` 是依賴注入的工廠函式，既有測試都以它建立應用。
- worker 以 `--pool=solo --concurrency=1` 執行 Celery，是單一程序。task 的實作是 `worker/celery_app.py` 裡的 `register_task`，它呼叫 `process_job(context, job_id)` 並回傳 `ProcessOutcome`（`succeeded`、`failed`、`skipped`）。`process_job` 不回傳影格數，影格在 `layout.frames_dir(job_id)`。
- Celery 設定 `task_acks_late=True`，所以正在處理中的工作不在 Redis 佇列裡。佇列是 Redis 的 list，預設名稱 `celery`。
- 專案沒有 `prometheus_client`，也沒有 `monitoring/` 目錄。
- compose 的 worker 要求 NVIDIA GPU，且依既有規格在沒有 GPU 時拒絕啟動。開發環境的 Docker daemon 是側車：bind mount 看不到本機路徑，但 build context 會被上傳。

## Goals / Non-Goals

**Goals:**
- 指標與警報都能在沒有 GPU、沒有叢集的情況下被自動測試驗證。
- 不改變既有行為：不疊加 overlay 時 compose 不變，既有 `process_job` 與 `create_app` 的測試不需修改。
- 監控元件預設只對本機開放。

**Non-Goals:**
- 不追求指標的長期相容性承諾；名稱與標籤是這一版的契約，dashboard、警報與測試都以它為準。
- 不處理多副本 worker 或多程序 api 的指標彙整（目前都是單程序，見 R3）。

## Decisions

### D1：指標放在獨立埠，不放在對外的 API 埠

api 與 worker 各開一個指標埠（預設 `9101` 與 `9102`，環境變數 `OPENMOT_API_METRICS_PORT`、`OPENMOT_WORKER_METRICS_PORT`），容器內綁 `0.0.0.0`，compose 不發佈到主機，只在內部網路給 Prometheus 抓取。api 以 `prometheus_client.start_http_server` 在背景執行緒提供，與 uvicorn 同程序。

- **為什麼**：api 的 `:8000` 會發佈到主機所有介面，若把 `/metrics` 掛在 app 上，就會從主機埠直接露出路由與內部狀態。
- **替代方案**：(a) app 內的 `/metrics` 路由加反向代理或 IP 過濾：多一個元件，且過濾容易寫錯；(b) 用第二個 uvicorn 伺服器掛 ASGI 指標 app：比 `start_http_server` 重，沒有額外好處。
- **代價**：README 的 API 表原本預告 `GET /metrics`，改為說明「指標在獨立埠，不屬於對外 API」；既有 `tracking-job-api` 規格不變。
- **為什麼 worker 也用同樣方式**：worker 本來就沒有 HTTP，且是單程序（solo pool），不需要 multiprocess 模式。

### D2：`metrics/` 套件，registry 可注入，不用全域預設

`metrics/definitions.py` 提供 `create_metrics(registry)`，回傳不可變的 `Metrics`（各指標物件的集合）；`metrics/http.py` 是 api 的 middleware；`metrics/queue.py` 是佇列深度的 collector；`metrics/server.py` 啟動指標伺服器。`create_app` 新增選用參數 `metrics: Metrics | None = None`，預設不記錄，因此既有測試不動。

- **為什麼**：全域預設 registry 會讓測試互相污染（同名指標重複註冊會丟例外）；函式式、可注入也與專案現有風格一致。

指標清單（名稱與標籤就是契約）：

| 指標 | 型別 | 標籤 |
|---|---|---|
| `openmot_http_requests_total` | counter | `method`、`route`、`status` |
| `openmot_http_request_duration_seconds` | histogram | `method`、`route` |
| `openmot_jobs_total` | counter | `outcome` |
| `openmot_job_duration_seconds` | histogram | （無） |
| `openmot_frames_processed_total` | counter | （無） |
| `openmot_queue_depth` | gauge（collector） | （無） |

histogram 的 bucket 依用途手動指定：請求延遲 `0.005` 到 `10` 秒；工作耗時 `1` 到 `1500` 秒（預設的工作時間上限是 1500 秒）。

### D3：路由標籤取自路由模板；找不到路由就歸為固定值

middleware 在請求結束後從 `request.scope["route"].path` 取得模板（`/jobs/{job_id}`）；沒有匹配的路由時標籤固定為 `unmatched`。狀態碼以回應的實際值記錄。

- **為什麼**：用實際路徑當標籤會讓標籤種類隨 job ID 與掃描流量無限增長（基數爆炸）。
- **替代方案**：用 `prometheus-fastapi-instrumentator`：多一個依賴，標籤行為要另外覆寫才能符合規格。

### D4：worker 在 task 包裝層記錄，不改 `process_job`

`register_task` 內的 `process` 呼叫 `process_job` 前先數 `frames_dir` 的檔案數、記下單調時鐘，結束後依 `ProcessOutcome` 記錄：`outcome` 計數一律加一；`succeeded` 與 `failed` 記錄耗時；只有 `succeeded` 增加影格計數；`skipped` 不記錄耗時與影格。單調時鐘與 registry 都由參數注入，測試不需要等待真實時間。

- **為什麼**：`process_job` 已有完整測試且負責狀態轉換，加入指標會混進與它無關的責任；包裝層本來就持有 `context.layout`，可以取得影格數。
- **替代方案**：讓 `process_job` 回傳影格數：改動它的回傳型別會牽動既有測試與呼叫端。

### D5：佇列深度由 api 在被抓取時量，Redis 失敗就不輸出

`metrics/queue.py` 是自訂 collector：被抓取時用專用的 Redis 連線（連線與讀取逾時各 2 秒）執行 `LLEN`，佇列名稱取自 Celery 設定的預設佇列。連線失敗或逾時時 `collect()` 不產生這個指標，其餘指標照常輸出。

- **為什麼**：回報 0 會讓人誤以為沒有積壓；不輸出才會在 dashboard 顯示「無資料」，並讓 `JobQueueBacklog` 不因此誤判。專用連線加逾時，避免 Redis 故障時整個抓取被拖住。
- **限制**：`task_acks_late=True`，已被取走但尚未完成的工作不在佇列裡，所以這個數字只代表「等待中」。

### D6：指標埠綁定失敗時降級，不讓服務啟動失敗

`metrics/server.py` 捕捉 `OSError`，把原因寫到標準錯誤並回傳「未啟動」，服務照常執行。

- **為什麼**：監控是輔助功能，不應該因為埠衝突而讓主要的追蹤服務無法啟動；錯誤有明確記錄，不是靜默失敗。
- **替代方案**：啟動即失敗：對運維者最明顯，但會讓一個輔助功能能中斷服務。

### D7：Prometheus 與 Grafana 的設定打包進映像，不用 bind mount

`monitoring/prometheus.Dockerfile` 與 `monitoring/grafana.Dockerfile` 以固定版本的官方映像為基底，`COPY` 設定、規則與 dashboard；overlay 用 `build:` 建置。

- **為什麼**：開發環境的 Docker daemon 是側車，bind mount 看不到本機路徑，而 build context 會被上傳；這個作法也在 CI 與任何遠端 daemon 上行為一致，並且不依賴主機路徑。
- **替代方案**：bind mount 設定檔：在側車環境會得到空目錄，無法在本機驗證。
- Prometheus 的資料放在具名 volume。Prometheus 與 Grafana 的對外埠寫成 `127.0.0.1:9090:9090` 與 `127.0.0.1:3000:3000`。Grafana 的密碼用 `${GRAFANA_ADMIN_PASSWORD:?…}`，未設定時 `compose config` 就失敗。基底映像的版本在實作時挑選並實際拉取確認，寫進 Dockerfile 與文件。

### D8：警報規則以 `promtool` 做單元測試，CI 安裝固定版本

規則在 `monitoring/rules/openmot.rules.yml`，測試在同目錄的 `openmot.rules.test.yml`，以 `promtool check rules` 與 `promtool test rules` 驗證。運算式的起始值：

| 規則 | 條件 |
|---|---|
| `ApiDown`、`WorkerDown` | 對應目標 `up == 0` 持續 1 分鐘 |
| `HighJobFailureRatio` | 15 分鐘內失敗數占（成功加失敗）超過 20%，且該區間內至少 5 個工作；持續 5 分鐘 |
| `JobQueueBacklog` | `openmot_queue_depth > 10` 持續 10 分鐘 |
| `HighApiErrorRatio` | 5 分鐘內 `5xx` 占全部請求超過 5%，且該區間至少 20 個請求；持續 5 分鐘 |

CI 的 `test` 工作新增獨立的步驟（不動既有的 kubectl、kustomize、kubeconform 步驟）：下載 Prometheus 發行檔、以 `sha256sum --check` 驗證、取出 `promtool`。版本與校驗碼在實作時從官方發行頁取得並記錄在 `docs/ci.md`。本機沒有 `promtool` 時，驗證規則的測試 skip 並說明原因，CI 則一定執行（比照 Kubernetes 清單測試的作法）。

- **為什麼要有 `for`**：避免單一次抓取失敗就觸發；**最小量門檻**避免只有一兩個工作時，一次失敗就讓比例到 100%。
- **替代方案**：只用 pyyaml 檢查規則檔的結構：抓不到 PromQL 寫錯，也無法證明「條件成立時真的會觸發」。

### D9：dashboard 是手寫的 JSON，以測試保證不引用不存在的指標

`monitoring/grafana/dashboards/openmot.json` 七個面板；測試解析 JSON，確認面板數量，並從所有查詢中抽出 `openmot_` 開頭的指標名稱，與 `create_metrics` 在一個測試用 registry 上實際輸出的名稱（含 `_bucket`、`_sum`、`_count`、`_total` 後綴）比對。`provisioning/` 的資料來源與 dashboard 提供者檔案由同一個測試目錄的測試檢查。

### D10：驗證分層，並如實標示做不到的部分

1. 單元測試：獨立 registry，涵蓋標籤、`skipped` 不計耗時、基數、Redis 失敗時佇列深度缺席。
2. 程序內整合測試：在隨機埠啟動真實的指標伺服器，用 HTTP 抓取並解析；api 的部分用 `TestClient` 驗證對外埠回 `404`。
3. 本機端到端（標記為 `integration`，沒有 Docker 時 skip）：疊加 overlay 啟動 Redis、api、Prometheus、Grafana；確認 api 目標 `up`、送請求後請求指標增加、不啟動 worker 時送一個工作使佇列深度為 1、Grafana 找得到 dashboard，且每個面板的查詢能被 Prometheus 的查詢 API 解析。
4. **worker 的端到端做不到**：worker 在沒有 GPU 時依規格拒絕啟動。worker 指標只由第 1、2 層驗證，文件與 README 如實標註；有 GPU 的環境可以直接用 overlay 驗證。`WorkerDown` 在沒有 worker 的堆疊上會觸發，這是正確行為，不是缺陷。

## Risks / Trade-offs

- **[R1] worker 指標沒有端到端證據** → 以程序內的真實 HTTP 伺服器驗證指標內容，並在文件與 README 明講。
- **[R2] 警報門檻與 dashboard 數值沒有真實流量可校準** → 標註為起始值；門檻寫在規則檔，調整不需改程式。
- **[R3] 單程序假設**：api 的指標伺服器與 worker 的記錄都假設單一程序；若日後 uvicorn 多 worker 或 Celery prefork，需改用 multiprocess 模式 → 在文件寫明，現在不處理。
- **[R4] Docker Hub 的映像版本與 promtool 發行檔日後可能變動** → 映像固定版本標籤、promtool 比對 SHA-256；升級時同步修改 Dockerfile、CI 與文件。
- **[R5] 指標埠綁定失敗時降級**意味著運維者可能沒注意到沒有指標 → 錯誤寫到標準錯誤，且 Prometheus 的 `up` 會是 0，`ApiDown`、`WorkerDown` 會觸發。
- **[R6] worker 影格數在處理前計算**：若處理期間影格目錄被改動，計數與實際處理的數量會不同 → 目錄在 Job 建立時已定案、處理中不會被寫入，可接受。

## Migration Plan

純新增：不疊加 overlay 時，compose 行為與埠完全不變；api 與 worker 多開一個未發佈的指標埠。回復方式是不使用 overlay，或還原本變更的提交。`publish` 在合併到 `main` 後會重新發佈映像（既有行為）。

## Open Questions

- Prometheus 與 Grafana 的具體版本標籤與 `promtool` 的發行版本，在實作時挑選並驗證；不影響規格與任務拆分。
