# observability Specification

## Purpose
讓運維者能在不翻日誌的情況下看見服務的健康狀態：請求延遲、工作成功與失敗、處理耗時、影格吞吐與佇列積壓；並在失敗率升高、服務停擺或佇列積壓時由警報規則標示出來。監控元件預設只對本機開放，指標不經由對外的 API。

## ADDED Requirements

### Requirement: Metrics are served on a dedicated port, not through the public API
api 與 worker SHALL 各自在獨立的埠以 Prometheus 文字格式提供指標。api 的指標 SHALL NOT 出現在對外的 API 埠上（對外埠的 `/metrics` SHALL 回應 `404`）。指標埠的編號 SHALL 可由環境變數設定，且 SHALL 有預設值。

#### Scenario: Scraping the api metrics port
- **WHEN** 對 api 的指標埠發出 `GET /metrics`
- **THEN** 回應 `200` 與 Prometheus 文字格式的指標

#### Scenario: Public port does not expose metrics
- **WHEN** 對 api 的對外埠發出 `GET /metrics`
- **THEN** 回應 `404`

#### Scenario: Scraping the worker metrics port
- **WHEN** 對 worker 的指標埠發出 `GET /metrics`
- **THEN** 回應 `200` 與 Prometheus 文字格式的指標

#### Scenario: Metrics port cannot be bound
- **WHEN** 指標埠已被其他程序占用
- **THEN** 服務在標準錯誤輸出記錄無法提供指標的原因，並繼續處理請求或工作，而不是啟動失敗

### Requirement: API requests are measured by route
api SHALL 記錄每個請求的延遲與請求數，並以路由模板（例如 `/jobs/{job_id}`）、HTTP 方法與狀態碼分類。實際的 job ID SHALL NOT 出現在任何標籤中。不符合任何路由的請求 SHALL 歸入同一個固定的標籤值，使標籤的種類不隨請求路徑無限增長。

#### Scenario: Request to a templated route
- **WHEN** 對 `/jobs/abc123` 發出 `GET`，回應 `404`
- **THEN** 請求數以路由 `/jobs/{job_id}` 與狀態碼 `404` 增加，輸出中沒有 `abc123`

#### Scenario: Request to an unknown path
- **WHEN** 對兩個不同的未知路徑各發出一次請求
- **THEN** 兩次請求歸入同一個固定的路由標籤值，指標的種類數不增加

#### Scenario: Server error is visible
- **WHEN** 某個請求以 `5xx` 結束
- **THEN** 該請求的狀態碼標籤為 `5xx` 的實際值，可用來計算錯誤比例

### Requirement: Job outcomes, duration and throughput are recorded by the worker
worker SHALL 在每次處理 Job 結束時記錄結果，結果只有 `succeeded`、`failed`、`skipped` 三種。處理耗時 SHALL 只計算實際處理的時間，`skipped`（重複投遞或已是終態）SHALL NOT 計入耗時。成功處理的影格數 SHALL 累計到影格計數。

#### Scenario: Succeeded job
- **WHEN** 一個含 N 張影格的 Job 處理成功
- **THEN** 結果為 `succeeded` 的計數加 1，耗時記錄一筆，影格計數增加 N

#### Scenario: Failed job
- **WHEN** 一個 Job 處理失敗
- **THEN** 結果為 `failed` 的計數加 1，耗時記錄一筆，影格計數不增加

#### Scenario: Skipped job
- **WHEN** 同一個 Job 被重複投遞而被略過
- **THEN** 結果為 `skipped` 的計數加 1，耗時與影格計數都不變

### Requirement: Queue depth reports waiting jobs and is absent when unknown
api 的指標埠 SHALL 在每次被抓取時回報等待處理的 Job 數（佇列長度）。已被 worker 取走、正在處理的 Job SHALL NOT 計入。Redis 無法連線時 SHALL 不輸出此指標，而不是輸出 0。

#### Scenario: Jobs are waiting
- **WHEN** 佇列中有 3 個尚未被取走的 Job 時抓取指標
- **THEN** 佇列深度為 3

#### Scenario: Redis unreachable
- **WHEN** Redis 無法連線時抓取指標
- **THEN** 其餘指標仍正常輸出，佇列深度不出現在輸出中，抓取本身不失敗

### Requirement: One overlay command starts Prometheus and Grafana
系統 SHALL 提供一個 compose overlay，與既有的 compose 檔疊加後以一個指令啟動 Prometheus 與 Grafana。Prometheus SHALL 抓取 api 與 worker 的指標埠並載入警報規則；Grafana SHALL 在啟動時自動載入資料來源與 dashboard，不需手動匯入。不疊加 overlay 時，既有的 compose 行為 SHALL 不變。

#### Scenario: Targets are scraped
- **WHEN** 疊加 overlay 啟動且 api 在執行
- **THEN** Prometheus 的 api 目標狀態為 `up`

#### Scenario: Dashboard is provisioned
- **WHEN** Grafana 啟動完成
- **THEN** 不經手動操作即可在 Grafana 找到 dashboard，且資料來源指向 Prometheus

#### Scenario: Base stack unchanged
- **WHEN** 只用既有的 compose 檔啟動
- **THEN** 不會啟動 Prometheus 或 Grafana，既有服務的設定與埠不變

### Requirement: Monitoring is exposed to the local host only and requires a password
Prometheus 與 Grafana SHALL 只綁定在主機的 `127.0.0.1`。api 與 worker 的指標埠 SHALL NOT 對主機發佈，只在 compose 內部網路供 Prometheus 抓取。Grafana 的管理員密碼 SHALL 來自環境變數；未設定時 SHALL 拒絕啟動，且 SHALL NOT 使用預設密碼。監控映像 SHALL 固定到明確的版本，不使用 `latest`。

#### Scenario: Ports bound to loopback
- **WHEN** 檢視 overlay 展開後的設定
- **THEN** Prometheus 與 Grafana 的對外埠都綁定 `127.0.0.1`，api 與 worker 的指標埠沒有任何主機埠對應

#### Scenario: Missing Grafana password
- **WHEN** 未設定 Grafana 管理員密碼的環境變數就展開或啟動 overlay
- **THEN** 展開失敗並指出缺少哪個變數

#### Scenario: Images are pinned
- **WHEN** 檢視 overlay 與它建置監控映像所用的基底映像
- **THEN** 每個基底映像都有明確的版本標籤，沒有 `latest` 或缺少標籤的映像；overlay 不依賴任何主機路徑（設定隨映像打包，不以掛載提供）

### Requirement: Alert rules are verified by unit tests
系統 SHALL 提供四條警報規則：服務停擺（`ApiDown`、`WorkerDown`：目標 `up` 為 0 持續一段時間）、`HighJobFailureRatio`（一段時間內失敗比例過高且工作數達到下限）、`JobQueueBacklog`（等待中的 Job 數持續過高）、`HighApiErrorRatio`（`5xx` 比例過高）。每條規則 SHALL 有單元測試，證明條件成立時會觸發、條件不成立時不會觸發。門檻 SHALL 在文件中標示為未經真實流量校準的起始值。

#### Scenario: Failure ratio above threshold
- **WHEN** 測試資料呈現失敗比例高於門檻且工作數達到下限
- **THEN** `HighJobFailureRatio` 觸發

#### Scenario: A single failure does not alert
- **WHEN** 測試資料只有極少數的工作，其中一個失敗
- **THEN** `HighJobFailureRatio` 不觸發

#### Scenario: Service down
- **WHEN** 測試資料中 api 目標的 `up` 持續為 0 超過設定的時間
- **THEN** `ApiDown` 觸發；`up` 為 1 時不觸發

#### Scenario: Rule file is invalid
- **WHEN** 規則檔含有無法解析的運算式
- **THEN** 規則檢查失敗，CI 不通過

### Requirement: The dashboard only shows metrics the services emit
dashboard SHALL 包含請求率、API 延遲（p95）、`5xx` 比例、工作結果、工作耗時（p50 與 p95）、影格吞吐與佇列深度七個面板。dashboard 引用的每個指標名稱 SHALL 是服務實際輸出的指標，避免面板永遠沒有資料。

#### Scenario: Panels are present
- **WHEN** 檢視 dashboard 的定義
- **THEN** 七個面板都存在，且每個面板至少有一個查詢

#### Scenario: Unknown metric in a query
- **WHEN** 某個面板的查詢引用了服務不會輸出的指標名稱
- **THEN** 檢查失敗並指出面板與指標名稱

### Requirement: Observability is documented with its limits
文件 SHALL 說明指標的清單與含義、如何啟動 overlay 與取得 dashboard、警報門檻的來源，並如實標示未驗證的部分：worker 指標在沒有 GPU 的環境無法端到端驗證、門檻與 dashboard 數值未經真實流量校準、Kubernetes 的監控清單不在範圍內。

#### Scenario: Limits are stated
- **WHEN** 讀取文件
- **THEN** 文件明確寫出上述三項限制，且沒有把未驗證的行為說成已驗證
