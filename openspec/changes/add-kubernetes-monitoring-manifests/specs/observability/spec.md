## MODIFIED Requirements

### Requirement: Alert rules are verified by unit tests
系統 SHALL 提供四條警報規則：服務停擺（`ApiDown`、`WorkerDown`：目標 `up` 為 0，或目標不存在，持續一段時間）、`HighJobFailureRatio`（一段時間內失敗比例過高且工作數達到下限）、`JobQueueBacklog`（等待中的 Job 數持續過高）、`HighApiErrorRatio`（`5xx` 比例過高）。每條規則 SHALL 有單元測試，證明條件成立時會觸發、條件不成立時不會觸發。門檻 SHALL 在文件中標示為未經真實流量校準的起始值。

#### Scenario: Failure ratio above threshold
- **WHEN** 測試資料呈現失敗比例高於門檻且工作數達到下限
- **THEN** `HighJobFailureRatio` 觸發

#### Scenario: A single failure does not alert
- **WHEN** 測試資料只有極少數的工作，其中一個失敗
- **THEN** `HighJobFailureRatio` 不觸發

#### Scenario: Service down
- **WHEN** 測試資料中 api 目標的 `up` 持續為 0 超過設定的時間
- **THEN** `ApiDown` 觸發；`up` 為 1 時不觸發

#### Scenario: Target absent
- **WHEN** 測試資料中 api 或 worker 的 `up` 序列不存在（例如 Pod 不存在或因排程失敗而 Pending，目標根本沒有被發現）持續超過設定的時間
- **THEN** `ApiDown` 或 `WorkerDown` 分別觸發；目標存在且 `up` 為 1 時不觸發

#### Scenario: Rule file is invalid
- **WHEN** 規則檔含有無法解析的運算式
- **THEN** 規則檢查失敗，CI 不通過

### Requirement: Observability is documented with its limits
文件 SHALL 說明指標的清單與含義、如何啟動 overlay 與取得 dashboard、警報門檻的來源，並如實標示未驗證的部分：worker 指標在沒有 GPU 的環境無法端到端驗證、門檻與 dashboard 數值未經真實流量校準、Kubernetes 的監控清單只經過渲染與結構測試而沒有在叢集上驗證（也沒有 Kubernetes 的 Grafana dashboard 載入方式）。

#### Scenario: Limits are stated
- **WHEN** 讀取文件
- **THEN** 文件明確寫出上述三項限制，且沒有把未驗證的行為說成已驗證
