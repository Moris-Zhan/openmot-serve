# object-storage Specification

## Purpose
讓服務可以選用 S3 相容的物件儲存取代共用的檔案系統：影格與 Result 經由 bucket 傳遞，api 與 worker 不需要共用磁碟；未設定時維持檔案系統，行為不變。

## Requirements

### Requirement: The storage backend is chosen by configuration and defaults to the filesystem
系統 SHALL 以 `OPENMOT_STORAGE_BACKEND` 選擇儲存後端，可用值為 `filesystem` 與 `s3`，未設定時為 `filesystem`，且未設定時的行為與提供這個選項之前相同。選擇 `s3` 時 SHALL 要求 `OPENMOT_S3_BUCKET`，並接受可選的 `OPENMOT_S3_ENDPOINT_URL` 與 `OPENMOT_S3_REGION`；憑證 SHALL 來自標準的 `AWS_ACCESS_KEY_ID` 與 `AWS_SECRET_ACCESS_KEY` 環境變數，且 SHALL NOT 出現在日誌、錯誤訊息或映像中。後端值不支援、選了 `s3` 卻沒有 bucket，或端點不是 `http://` 或 `https://` 開頭，SHALL 使 api 與 worker 在處理任何請求或 Job 之前以非 0 的結束碼結束並指名有問題的設定。`OPENMOT_STORAGE_DIR` 在兩種後端都 SHALL 保留作為本機暫存，且在 `s3` 後端不需要在 api 與 worker 之間共用。

#### Scenario: Default backend is unchanged
- **WHEN** 未設定 `OPENMOT_STORAGE_BACKEND` 啟動 api 與 worker
- **THEN** 兩者使用共用檔案系統，行為與先前相同，不需要任何 S3 設定

#### Scenario: S3 backend without a bucket
- **WHEN** 設定 `OPENMOT_STORAGE_BACKEND=s3` 但沒有 `OPENMOT_S3_BUCKET`
- **THEN** 啟動以非 0 的結束碼結束，訊息指名 `OPENMOT_S3_BUCKET`

#### Scenario: Unsupported backend
- **WHEN** 設定 `OPENMOT_STORAGE_BACKEND=gcs`
- **THEN** 啟動以非 0 的結束碼結束，訊息指名該設定與可用值

#### Scenario: Credentials stay out of the output
- **WHEN** 以 S3 後端啟動，且設定了金鑰
- **THEN** 日誌與錯誤訊息不含金鑰的值

### Requirement: With the S3 backend, uploaded frames are stored in the bucket
使用 `s3` 後端時，api 在驗證並解開上傳之後 SHALL 把每個影格以 `jobs/<job_id>/frames/<檔名>` 為鍵上傳到 bucket，並 SHALL 在回應 `202` 之前完成；本機暫存 SHALL 在提交結束後被清除。任何一個影格上傳失敗時，api SHALL 刪除該 Job 已上傳的物件、不建立 Job、回應 `5xx` 並說明無法儲存上傳，使 bucket 不留下半個 Job。入隊失敗時 SHALL 刪除該 Job 在 bucket 的所有物件。

#### Scenario: Frames are in the bucket after submission
- **WHEN** 使用者提交一個有 N 張影格的有效序列
- **THEN** bucket 內 `jobs/<job_id>/frames/` 下恰有 N 個物件，api 的本機暫存目錄內沒有該序列的檔案

#### Scenario: An upload fails halfway
- **WHEN** 上傳到第 K 張時物件儲存拒絕寫入
- **THEN** api 回應 `5xx`，bucket 內沒有該 Job 的任何物件，也沒有建立 Job

#### Scenario: The queue is unavailable
- **WHEN** 影格已上傳但入隊失敗
- **THEN** api 回應 `503`，該 Job 在 bucket 的物件被刪除，Job Status 為 `failed`

### Requirement: With the S3 backend, the worker tracks from the bucket and stores the Result there
使用 `s3` 後端時，worker SHALL 在處理 Job 時先把 `jobs/<job_id>/frames/` 下載到本機暫存、用與檔案系統後端相同的追蹤流程處理，再把 Result 與中繼檔以 `jobs/<job_id>/result.txt` 與 `jobs/<job_id>/result.meta.json` 為鍵上傳，之後才把 Job 標為 `succeeded`。同一個序列與同一組設定下，S3 後端的 Result 與中繼檔 SHALL 與檔案系統後端逐位元組相同。無論成功或失敗，本機暫存 SHALL 在 Job 結束時被清除；Job 失敗時 SHALL 不在 bucket 留下 Result 物件。下載或上傳失敗 SHALL 使 Job 進入 `failed` 並記錄原因，而不是讓 worker 結束。Job 開始前若本機已有同一個 Job 的殘留暫存（例如 worker 在上次處理途中被終止），SHALL 先清除再下載。

#### Scenario: A Job succeeds through the bucket
- **WHEN** worker 處理一個影格已在 bucket 的 Job
- **THEN** Job 進入 `succeeded`，bucket 內有 `result.txt` 與 `result.meta.json`，worker 的本機暫存為空

#### Scenario: The served Result equals the filesystem backend
- **WHEN** 以同一個序列與同一組設定分別用 `filesystem` 與 `s3` 後端處理
- **THEN** 兩者的 Result 與中繼檔逐位元組相同

#### Scenario: The frames cannot be downloaded
- **WHEN** worker 下載影格時物件儲存失敗
- **THEN** Job 進入 `failed` 並記錄原因，bucket 內沒有該 Job 的 Result 物件，worker 繼續處理下一個 Job

#### Scenario: Tracking fails
- **WHEN** 追蹤過程拋出例外
- **THEN** Job 進入 `failed`，本機暫存被清除，bucket 內沒有該 Job 的 Result 物件

#### Scenario: Leftover scratch from an interrupted run
- **WHEN** 同一個 Job 的本機暫存已存在而 worker 重新處理它
- **THEN** worker 先清除殘留再下載，處理結果不含殘留的檔案

### Requirement: With the S3 backend, the API serves the Result and its health from the bucket
使用 `s3` 後端時，`GET /jobs/{job_id}/result` SHALL 從 bucket 讀取 Result，並對 Job 狀態、404 與 4xx 的回應與檔案系統後端相同；已 `succeeded` 的 Job 的 Result 物件不存在時 SHALL 回應 `500` 並指出 Result 遺失。`GET /health` SHALL 在 bucket 可連線且本機暫存可寫入時回應 `200`；bucket 不存在或不可連線時 SHALL 回應 `503` 並指出物件儲存不可用，且訊息不含金鑰。

#### Scenario: Succeeded job
- **WHEN** 呼叫端對一個 `succeeded` 的 Job 取回 Result
- **THEN** 回應內容與該 Job 在 bucket 的 `result.txt` 相同，每行 10 個欄位

#### Scenario: The Result object is missing
- **WHEN** 一個 `succeeded` 的 Job 在 bucket 沒有 Result 物件
- **THEN** api 回應 `500` 並指出該 Job 的 Result 遺失

#### Scenario: Healthy
- **WHEN** bucket 可連線且本機暫存可寫入時呼叫 `/health`
- **THEN** 回應 `200`

#### Scenario: The object store is down
- **WHEN** 物件儲存不可連線或 bucket 不存在時呼叫 `/health`
- **THEN** 回應 `503`，訊息指出物件儲存不可用且不含金鑰
