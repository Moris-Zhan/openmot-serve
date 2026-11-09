# Spec Delta

## Purpose

讓呼叫端以 HTTP 提交一個 Sequence 作為 Tracking Job、不必等待追蹤完成，並在之後查詢 Job Status、取回 Result；同時把不受信任的上傳在入口處擋下，避免壞資料進入佇列。

## ADDED Requirements

### Requirement: Submitting a Sequence creates a Tracking Job without waiting for tracking
系統 SHALL 在 `POST /jobs` 收到一個合法的影格 zip 後，立即回應 `202 Accepted` 與一個唯一的 job ID，不等待追蹤完成。回應 SHALL 在 Job 已被持久化並排入佇列之後才送出。

#### Scenario: Valid frame archive
- **WHEN** 呼叫端以 `POST /jobs` 上傳一個含有連續編號影格的 zip
- **THEN** 系統回應 `202 Accepted`，內容含 job ID，且該 Job 的 Job Status 為 `queued`

#### Scenario: Each submission gets its own Job
- **WHEN** 呼叫端連續提交兩次相同的 zip
- **THEN** 系統回傳兩個不同的 job ID，且兩個 Job 各自獨立處理

### Requirement: Invalid uploads are rejected at submission
系統 SHALL 在建立 Job 之前驗證上傳，並以 4xx 回應與可讀的原因拒絕不合法的輸入，且不得建立 Job、不得留下任何已解壓的檔案。不合法包含：不是 zip、zip 內沒有影格、影格編號不連續或無法讀取、超過大小上限、解壓後超過總大小上限、zip 內含會寫到目標資料夾之外的路徑。

#### Scenario: Not a zip archive
- **WHEN** 呼叫端上傳的檔案不是有效的 zip
- **THEN** 系統回應 4xx 與原因，且不建立 Job

#### Scenario: Archive without frames
- **WHEN** 上傳的 zip 內沒有任何影格
- **THEN** 系統回應 4xx 與原因，且不建立 Job

#### Scenario: Path escaping the target folder
- **WHEN** 上傳的 zip 內含指向目標資料夾之外的路徑（例如 `../outside.jpg`）
- **THEN** 系統回應 4xx，沒有任何檔案被寫到目標資料夾之外，且不建立 Job

#### Scenario: Oversized upload
- **WHEN** 上傳大小或解壓後的總大小超過系統設定的上限
- **THEN** 系統回應 4xx 與原因，且不建立 Job

### Requirement: Job Status can be queried
系統 SHALL 在 `GET /jobs/{job_id}` 回傳該 Job 目前的 Job Status，值只會是 `queued`、`running`、`succeeded`、`failed` 之一；Job Status 為 `failed` 時 SHALL 同時回傳失敗原因。未知的 job ID SHALL 回應 `404`。

#### Scenario: Known job
- **WHEN** 呼叫端查詢一個已提交的 Job
- **THEN** 系統回傳其 Job Status，且狀態只沿 `queued` → `running` → `succeeded` 或 `failed` 前進，不會倒退

#### Scenario: Failed job reports a reason
- **WHEN** 呼叫端查詢一個 Job Status 為 `failed` 的 Job
- **THEN** 回應包含失敗原因的文字

#### Scenario: Unknown job
- **WHEN** 呼叫端查詢一個從未建立的 job ID
- **THEN** 系統回應 `404`

### Requirement: Result is available only for a succeeded Job
系統 SHALL 在 `GET /jobs/{job_id}/result` 對 Job Status 為 `succeeded` 的 Job 回傳 MOTChallenge 格式的 Result，內容與 `sequence-tracking` 的輸出一致。Job 尚未完成或失敗時 SHALL 不回傳 Result，並以可區分的 4xx 回應說明目前狀態；未知的 job ID SHALL 回應 `404`。

#### Scenario: Succeeded job
- **WHEN** 呼叫端對一個 `succeeded` 的 Job 取回 Result
- **THEN** 系統回傳每行 10 個欄位的 MOTChallenge 格式內容

#### Scenario: Job not finished
- **WHEN** 呼叫端對 `queued` 或 `running` 的 Job 取回 Result
- **THEN** 系統回應 4xx 並指出 Job 尚未完成，不回傳任何部分內容

#### Scenario: Failed job
- **WHEN** 呼叫端對 `failed` 的 Job 取回 Result
- **THEN** 系統回應 4xx 並指出 Job 已失敗，不回傳 Result

### Requirement: Health endpoint reports whether the service can accept jobs
系統 SHALL 提供 `GET /health`：服務可以接受 Job（佇列後端可連線、共用儲存可寫入）時回應 `200`；否則回應 `503` 並指出哪一項不可用。

#### Scenario: Healthy
- **WHEN** 佇列後端可連線且共用儲存可寫入時呼叫 `/health`
- **THEN** 系統回應 `200`

#### Scenario: Queue backend down
- **WHEN** 佇列後端無法連線時呼叫 `/health`
- **THEN** 系統回應 `503` 並指出佇列後端不可用
