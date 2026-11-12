# 可觀測性指南

本文說明 api 與 worker 提供的 Prometheus 指標。指標的名稱與標籤由測試與程式保持一致；數值的意義還沒有在真實流量下驗證。

## 指標

api 與 worker 各自在獨立的埠提供 Prometheus 文字格式的指標（`GET /metrics`）。這兩個埠不是對外 API 的一部分：對外的 API 埠（預設 8000）沒有 `/metrics`，請求會得到 `404`。容器內監聽 `0.0.0.0`，但 compose 不會把它們發佈到主機，只在內部網路供 Prometheus 抓取。

| 服務 | 環境變數 | 預設埠 |
|---|---|---|
| api | `OPENMOT_API_METRICS_PORT` | 9101 |
| worker | `OPENMOT_WORKER_METRICS_PORT` | 9102 |

埠已被占用時，服務會在標準錯誤輸出無法提供指標的原因，並照常運作，不會因此啟動失敗。這時 Prometheus 對該目標的 `up` 會是 0。

| 指標 | 型別 | 標籤 | 含義 |
|---|---|---|---|
| `openmot_http_requests_total` | counter | `method`、`route`、`status` | api 處理的請求數。`route` 是路由模板（如 `/jobs/{job_id}`），不含實際 job ID；沒有匹配的路由一律是 `unmatched` |
| `openmot_http_request_duration_seconds` | histogram | `method`、`route` | api 處理請求的時間，bucket 從 5 毫秒到 10 秒 |
| `openmot_jobs_total` | counter | `outcome` | worker 處理完的工作，`outcome` 為 `succeeded`、`failed`、`skipped`（重複投遞或已是終態而被略過） |
| `openmot_job_duration_seconds` | histogram | 無 | 實際執行的工作的處理時間，不含排隊，也不含 `skipped`；bucket 從 1 秒到 1500 秒（預設的工作時間上限） |
| `openmot_frames_processed_total` | counter | 無 | 成功的工作所處理的影格數，用 `rate()` 可得到吞吐（FPS） |
| `openmot_queue_depth` | gauge | 無 | 等待 worker 取走的工作數，由 api 的指標埠在被抓取時讀 Redis；Redis 無法連線時不輸出此指標，而不是輸出 0 |

佇列深度只計等待中的工作：worker 已取走、正在處理的工作不在 Redis 佇列裡，所以不會被算進去。

## worker 指標

worker 在啟動成功（環境檢查與權重載入都通過）之後、開始接工作之前才開指標埠。啟動失敗的 worker 不會開埠，所以 Prometheus 對它的 `up` 會是 0。

記錄發生在處理工作的 task 包裝層，`process_job` 本身沒有改動：

- `openmot_jobs_total` 每處理完一個工作加一，依結果分成 `succeeded`、`failed`、`skipped`。
- `openmot_job_duration_seconds` 只記實際執行的工作（`succeeded` 與 `failed`），`skipped` 不記，避免把空轉記成 0 秒。
- `openmot_frames_processed_total` 在工作成功時加上該工作的影格數；影格數是處理前數的，失敗或略過的工作不計。

在沒有 GPU 的環境，worker 依規格拒絕啟動，所以 worker 指標無法端到端驗證。目前只有程序內的測試證明：用真實的指標伺服器，執行一次工作後，抓到的輸出含有預期的計數。有 GPU 的環境才能用真實的 worker 驗證。

## 單程序假設

api 是單一程序（一個 uvicorn），worker 是單一程序（Celery 的 solo pool），指標都存在各自的程序記憶體裡，由同一程序內的指標伺服器輸出。如果日後改成多個 uvicorn worker 或 Celery 的 prefork 池，每個子程序會各有一份計數，必須改用 `prometheus_client` 的 multiprocess 模式才能正確彙整；目前沒有處理這個情況。

## 啟動監控

監控是一個 compose overlay，疊在既有的 `deploy/docker/docker-compose.yml` 上；不疊加時，既有堆疊的行為與埠都不變。

```bash
cd deploy/docker
export GRAFANA_ADMIN_PASSWORD='請自己選一個密碼'
docker compose -f docker-compose.yml -f docker-compose.monitoring.yml up --build
```

| 項目 | 位置 | 說明 |
|---|---|---|
| Prometheus | `http://127.0.0.1:9090` | 埠可用 `OPENMOT_PROMETHEUS_PORT` 改 |
| Grafana | `http://127.0.0.1:3000` | 帳號 `admin`，密碼是你設定的 `GRAFANA_ADMIN_PASSWORD`；埠可用 `OPENMOT_GRAFANA_PORT` 改 |

- **只對本機開放**：Prometheus 與 Grafana 的對外埠都綁在 `127.0.0.1`。api 與 worker 的指標埠不發佈到主機，只在 compose 的內部網路讓 Prometheus 抓取。
- **密碼必填**：沒有預設密碼。沒設定 `GRAFANA_ADMIN_PASSWORD` 時，`docker compose` 會在展開設定時失敗並指出這個變數，而不是用預設值啟動。
- **設定打包進映像**：Prometheus 的設定與 Grafana 的 provisioning 是用 `COPY` 打包進映像，不是掛載主機路徑，所以在遠端的 Docker daemon 上行為也一樣。代價是改了 `monitoring/` 底下的設定後，要加 `--build` 重新建置才會生效。
- **沒有 GPU 的機器**：worker 依規格不會啟動，可以只啟動其餘的服務：`docker compose -f docker-compose.yml -f docker-compose.monitoring.yml up --build redis api prometheus grafana`。這時 Prometheus 對 worker 的目標會是 down，這是預期的。

## 警報規則

規則在 `monitoring/rules/openmot.rules.yml`，由 Prometheus 載入並評估；在 Prometheus 的介面（`http://127.0.0.1:9090/alerts`）看得到狀態。

| 警報 | 條件 | 嚴重度 |
|---|---|---|
| `ApiDown` | api 的目標 `up == 0` 持續 1 分鐘 | critical |
| `WorkerDown` | worker 的目標 `up == 0` 持續 1 分鐘 | critical |
| `HighJobFailureRatio` | 過去 15 分鐘失敗的工作占（成功加失敗）超過 20%，且這段時間至少有 5 個工作；持續 5 分鐘 | warning |
| `JobQueueBacklog` | 等待中的工作超過 10 個，持續 10 分鐘 | warning |
| `HighApiErrorRatio` | 過去 5 分鐘 `5xx` 占全部請求超過 5%，且這段時間至少有 20 個請求；持續 5 分鐘 | warning |

- **門檻是起始值**：這些數字沒有根據任何真實流量決定，未經真實流量校準，只是讓規則一開始就能運作的合理猜測。有了實際的流量與工作量之後，應該回頭調整規則檔裡的數字。
- **最小量門檻**：失敗比例與錯誤比例都要求最少的工作數或請求數，避免只有一兩個工作時，一次失敗就讓比例變成 100% 而誤報。
- **沒有通知**：沒有接 Alertmanager，也沒有任何通知管道（電子郵件、聊天訊息）。警報只會出現在 Prometheus 的介面，沒有人會被叫醒。
- **沒有 worker 的堆疊**：在沒有 GPU 而只啟動 api 的堆疊上，`WorkerDown` 會觸發，這是預期的，不是錯誤。

每條警報都有單元測試，證明條件成立時會觸發、不成立時不會觸發，並且包含「比例是 100% 但數量太少」的案例。在專案根目錄執行（需要 `promtool`，它在 Prometheus 的發行檔裡）：

```bash
promtool check rules monitoring/rules/openmot.rules.yml
promtool test rules monitoring/rules/openmot.rules.test.yml
```

## dashboard

啟動監控後，開啟 `http://127.0.0.1:3000`，用帳號 `admin` 與你設定的密碼登入，在 Dashboards 裡找名為 `openmot` 的 dashboard（`uid` 是 `openmot-overview`）。資料來源與 dashboard 都是啟動時自動載入的，不需要手動匯入；因為是從檔案載入，在介面上做的修改不會被保存，要改請改 `monitoring/grafana/dashboards/openmot.json` 後重新建置映像。

| 面板 | 看什麼 |
|---|---|
| 請求率 | api 每秒處理的請求數，依路由模板分類 |
| API 延遲 (p95) | api 處理請求的第 95 百分位時間，依路由模板分類 |
| 5xx 比例 | 以 5xx 結束的請求占全部請求的比例 |
| 工作結果 | worker 每 5 分鐘處理完的工作數，依 `succeeded`、`failed`、`skipped` 分類 |
| 工作耗時 (p50 / p95) | 實際執行的工作的處理時間，不含排隊與略過的工作 |
| 影格吞吐 (FPS) | 成功的工作每秒處理的影格數 |
| 佇列深度 | 等待 worker 取走的工作數 |

每個面板的查詢都只用服務實際輸出的指標，由測試保證。沒有流量時面板會是空的，這是預期的：直方圖的百分位與比例在沒有請求或工作時沒有資料，佇列深度在 Redis 無法連線時也沒有資料。面板上的數字還沒有在真實流量下檢視過。

## 驗證與限制

**已驗證**

- 單元測試與程序內測試：指標的名稱、標籤與 bucket；路由模板與 `unmatched`；`skipped` 不計耗時；Redis 失敗時佇列深度缺席；埠被占用時服務照常運作；設定值的檢查。
- 警報規則：用 `promtool` 做單元測試，每條規則都有觸發與不觸發的案例，並以突變驗證確認測試真的會抓到門檻與最小量條件被改掉。
- 在本機的 Docker 上疊加 overlay、啟動 Redis、api、Prometheus、Grafana（不含 worker）做端到端驗證，全部通過：Prometheus 抓取 api 的目標為 `up`、worker 的目標為 `down`；送出請求後請求指標依路由模板增加，且實際的 job ID 不在標籤裡；沒有 worker 時送出一個工作，佇列深度為 1；對外 API 埠的 `/metrics` 是 `404`，指標埠不對主機開放；五條警報規則都被 Prometheus 載入，`WorkerDown` 離開了 inactive 狀態；Grafana 自動載入資料來源與 dashboard，每個面板的查詢都被 Prometheus 接受。

**未驗證，或不在範圍**

- **worker 指標沒有端到端證據**：worker 依規格在沒有 GPU 的環境拒絕啟動，所以 worker 的指標無法端到端驗證，只有程序內的測試（真實的指標伺服器加一次模擬的工作）。有 GPU 的環境可以用同一個 overlay 驗證。
- **數值未經真實流量校準**：警報門檻與 dashboard 都是在沒有真實流量的情況下設計與驗證的，面板上的數字沒有在真實的流量與工作量下檢視過。
- **Kubernetes 的監控清單不在範圍內**：這個變更只提供 compose overlay，ServiceMonitor 與 monitoring namespace 沒有做。
- **沒有 Alertmanager**：警報不會通知任何人。
- **CI 的 promtool 步驟尚未在 GitHub 的 runner 上執行**：工作流程已通過語法檢查與結構測試，下載與校驗的指令在本機以相同的版本與校驗碼實際執行過，但還沒有在 GitHub 的託管 runner 上跑過。
