# Spec Delta

## Purpose

讓排入佇列的 Tracking Job 被可靠地執行：用 v0.1 的追蹤管線處理 Sequence、把 Result 與 Job Status 如實記錄，並且在任何失敗下都讓 Job 進入可被查詢的終態，而不是卡在 `running` 或留下不完整的 Result。

## ADDED Requirements

### Requirement: Queued Jobs are processed with the existing tracking pipeline
系統 SHALL 從佇列取出 Tracking Job，以 `sequence-tracking` 相同的管線與預設追蹤參數處理其 Sequence，並把 Result 寫到共用儲存。同一份 Sequence 與相同設定所得的 Result SHALL 與命令列的結果逐位元組相同。

#### Scenario: Job runs to completion
- **WHEN** worker 取得一個 `queued` 的 Job
- **THEN** Job Status 先變為 `running`，處理完成後變為 `succeeded`，且 Result 已完整寫入

#### Scenario: Same output as the command line
- **WHEN** 以相同的影格、權重、精度與追蹤參數，分別透過服務與命令列處理
- **THEN** 兩者的 Result 內容完全相同

### Requirement: Every Job ends in a terminal state with a recorded reason
系統 SHALL 確保每個被取出的 Job 最終進入 `succeeded` 或 `failed`。處理過程中發生任何錯誤（影格損毀、偵測失敗、權重驗證失敗、儲存空間不足）時，Job Status SHALL 變為 `failed` 並記錄失敗原因，且不得留下不完整的 Result。

#### Scenario: Corrupt frame during processing
- **WHEN** Sequence 中有一個影格在處理時無法讀取
- **THEN** Job Status 變為 `failed`，記錄指出無法讀取的影格，且沒有 Result 檔

#### Scenario: Result is never partially visible
- **WHEN** 處理進行到一半
- **THEN** 共用儲存中不存在該 Job 的 Result 檔，直到完整寫入後才出現

#### Scenario: One failing Job does not stop the worker
- **WHEN** 一個 Job 失敗後佇列中還有其他 Job
- **THEN** worker 繼續處理後續的 Job

### Requirement: A Job is processed once
系統 SHALL 不讓同一個 Job 被兩個 worker 同時處理，也 SHALL 不在 worker 當機後讓 Job 永遠停在 `running`。

#### Scenario: Worker crashes mid-job
- **WHEN** worker 在處理 Job 的途中被終止
- **THEN** 該 Job 不會停留在 `running`：它被重新排入並完成，或被標為 `failed` 並註明原因

#### Scenario: Duplicate delivery
- **WHEN** 同一個 Job 被投遞兩次
- **THEN** 只有一次實際處理，Result 內容不受影響

### Requirement: The detector is loaded once per worker
系統 SHALL 在 worker 啟動時載入偵測器並驗證權重完整性，之後處理的每個 Job 重用它；權重驗證失敗時 worker SHALL 拒絕處理 Job 並回報原因。

#### Scenario: Weights fail verification
- **WHEN** 權重檔的 SHA-256 與預期不符
- **THEN** worker 啟動失敗並指出原因，不處理任何 Job

#### Scenario: Consecutive Jobs
- **WHEN** worker 連續處理兩個 Job
- **THEN** 第二個 Job 不再重新載入權重
