# Design

## Context

動機見 proposal.md 的 Why，行為合約見三份 spec。現況（已唯讀確認）：

- v0.1 已提供函式庫式的追蹤：`tracking.sequence.track_sequence(frames_dir, output, detect_frame, metadata)` 先寫暫存檔再改名，`tracking.run.load_default_detect_frame(weights, precision, device)` 載入偵測器，`tracking.env_check.run_env_check` 檢查 GPU。v0.1 調出的預設追蹤參數（0.6、0.9、30）就是 `TrackerParams` 的預設值，服務直接用預設即可。
- `pyproject.toml` 只打包 `tracking*`、`eval*`；沒有 `app/`、`worker/`、`deploy/`，也沒有任何 Docker 檔案。依賴為 torch 2.7.1（cu128 索引）、numpy、scipy、opencv-python-headless。
- 開發環境是 conda 環境 `openmot`，套件以 `uv pip install` 安裝；專案不使用 `uv sync`（見已歸檔的 v0.1 design D1）。
- 目標 GPU 為 RTX 5090，需要 CUDA 12.8 以上；PyTorch 的 cu128 wheel 已內含 CUDA 執行階段，容器只需要主機驅動與 NVIDIA Container Toolkit。
- 領域詞彙以 CONTEXT.md 為準（Tracking Job、Job Status、Result）。

## Goals / Non-Goals

**Goals:**
- 提交與處理解耦：API 回應時間與追蹤時間無關。
- 任何失敗下 Job 都進入可查詢的終態，Result 不會半成品可見。
- 不需要 GPU、Redis、Docker 的預設測試也能涵蓋 API、驗證、狀態機與 worker 邏輯。

**Non-Goals:**
- 不做 Job 保留期限與清理、速率限制、驗證授權、多租戶。
- 不做水平擴充 worker 的調校；v0.2 是單一 GPU worker。多副本與排程留給 v0.3。
- 不改動 `tracking/` 與 `eval/` 的行為。

## Decisions

### D1：程式碼分成 `app/`（API）與 `worker/`，共用小型的 `jobs` 核心
`app/` 只負責 HTTP、上傳驗證與提交；`worker/` 只負責取出 Job 並呼叫管線；兩者共用的 Job 紀錄、狀態轉移與儲存路徑規則放在一個不依賴 FastAPI 與 Celery 的模組。依賴放進 `pyproject.toml` 的 optional extras（`service`），核心的 `tracking`、`eval` 不因此多出依賴。依專案慣例以函式與凍結的 dataclass 為主，I/O 邊界（Redis、檔案系統、佇列）以參數傳入，使測試可以注入假的實作。
*替代方案*：單一套件、單一映像——api 與 worker 的依賴與體積差很多（見 D8），且職責混在一起。

### D2：Job Status 存在 Redis 的自有紀錄，不使用 Celery 的結果後端
每個 Job 一筆 Redis hash（鍵含 job ID），欄位為 `status`、`reason`、建立與更新時間；狀態轉移以原子操作完成，只允許 `queued → running → succeeded|failed` 與 `queued → failed`，終態不可被覆寫。
*為什麼不用 Celery 結果後端*：未知的 job ID 在 Celery 中會回報為 `PENDING`，與 spec 要求的 `404` 衝突；自有紀錄可以明確區分「不存在」與「排隊中」，並能存失敗原因。
*替代方案*：把狀態放在檔案系統——要做原子轉移與 API／worker 共用鎖，比 Redis 麻煩。

### D3：共用儲存的目錄配置與原子性
共用 volume 根目錄下，每個 Job 一個資料夾：`jobs/<job_id>/frames/`（解壓後的影格）與 `jobs/<job_id>/result.txt`（Result）。API 先在同一個檔案系統內的暫存資料夾驗證並解壓，成功後才以 rename 移到 `jobs/<job_id>/frames/`，再寫入 Redis 並排入佇列，所以失敗的上傳不會留下殘檔，被排入的 Job 一定已有完整的影格。Result 沿用 `track_sequence` 的「寫暫存檔再改名」。
排入佇列失敗時（Redis 掛了）API 回應 5xx，並移除剛放好的影格。

### D4：上傳驗證放在 API 入口，zip 只接受平鋪的影格
影格 zip 要求影格檔放在 zip 根層（`cd frames && zip ../seq.zip *.jpg`），不接受子資料夾；zip 內只要出現任何不是影像檔的項目，整個上傳就被拒絕（嚴格比寬鬆更容易說明與測試，之後可放寬而不破壞相容性）。驗證順序：串流寫入暫存檔並在超過大小上限時中止 → 確認是 zip → 逐一檢查項目名稱（拒絕絕對路徑、含 `..`、含子資料夾、非影像副檔名）→ 以宣告大小與實際解壓位元組數雙重限制解壓總量（防壓縮炸彈）→ 用 `tracking.sequence.list_frames` 確認編號連續，並用 OpenCV 讀取每個影格確認可解碼。上限為環境變數設定，預設值寫在設定模組。
*替代方案*：依賴 `zipfile.extractall` 的預設安全性——不足以防壓縮炸彈與 Windows 風格路徑，不採用。

### D5：Celery 設定偏向「不遺失、不重複」，單一 GPU worker 序列化處理
`acks_late` 與 `reject_on_worker_lost` 開啟，使 worker 當機時任務被重新投遞；`prefetch_multiplier=1`、單一並行，避免同一 GPU 上同時跑多個 Job 與提前佔走任務。
重複處理的防護：worker 取得任務後先以 Redis 上的「每 Job 鎖」（`SET NX` 加逾時）宣告擁有權；鎖被他人持有表示是重複投遞，直接確認並丟棄。鎖的逾時等於佇列的 visibility timeout，因此「鎖仍有效」就代表前一個處理者還活著，鎖過期後被重新投遞的任務則可接手（前一個 worker 視為已死）。Job 已是終態時，任務直接結束，不重做。
為了避免合法的長 Job 被誤判為死亡，另設任務的時間上限，且小於鎖的逾時；超時時 Job 標為 `failed` 並註明原因。
*替代方案*：Celery 的 `task_acks_late` 單獨使用——沒有鎖時，visibility timeout 到期會讓仍在執行的 Job 被另一個 worker 重複取走。

### D6：偵測器在 worker 啟動時載入一次，使用 solo pool
CUDA 在 fork 出來的子行程中容易出問題，所以 worker 用 solo pool（單行程單執行緒），在啟動階段先跑環境檢查，再載入權重與偵測器並保存在行程層級的唯讀物件；環境檢查或權重驗證失敗就讓行程以非 0 結束碼結束，符合 spec 的「不退回 CPU」與「權重驗證失敗不處理 Job」。啟動後的每個 Job 重用同一個偵測器。
*替代方案*：每個 Job 重新載入——權重為數百 MB，載入時間會超過許多短 Sequence 的追蹤時間。

### D7：設定一律來自環境變數，並在啟動時驗證
儲存根目錄、Redis URL、上傳大小與解壓總量上限、權重路徑、推論精度、鎖逾時與任務時間上限都由環境變數提供，集中在一個設定函式中解析與驗證，缺值或非法值在啟動時就失敗並指出是哪一個變數。追蹤參數 v0.2 使用 `TrackerParams` 預設值，不開放每個 Job 自訂（避免未驗證的輸入影響結果與 v0.1 的可重現性）。

### D8：映像與 compose
- 基底為 `python:3.11-slim`，用 `uv pip install` 安裝；worker 映像安裝含 torch（cu128）的相依，不內含 CUDA 工具鏈，GPU 由 NVIDIA Container Toolkit 掛入。api 映像不含 torch。已驗證（任務 1.3）：`app`、`worker`、`jobs.settings`、`tracking.sequence`、`tracking.config`、`tracking.motchallenge` 都不會 import torch，在沒有 torch 的乾淨虛擬環境中可正常 import（環境約 358 MB，含 torch 的開發環境約 6.9 GB），因此不需要拆出獨立模組，並由測試守住「這些模組不得載入 torch」。但 `pyproject.toml` 的核心依賴包含 torch，所以 api 映像要用 `uv pip install --no-deps .` 加上明確的依賴清單（numpy、scipy、opencv-python-headless 與 `service` extras），不能直接安裝整個專案；清單與 pyproject 的對應由任務 6.1 檢查。
- 權重不進映像：以唯讀 bind mount 掛入，worker 載入時驗證 SHA-256。`.dockerignore` 排除 `data/`、`models/`、`.git`、`*.pth`。
- compose 有三個服務：`redis`（啟用 AOF 並掛 volume，重建容器不丟狀態）、`api`、`worker`（以 compose 的 GPU 預約宣告取得 NVIDIA 裝置）。`api` 與 `worker` 依 Redis 的健康檢查啟動；每個服務都有自己的健康檢查（API 打 `/health`，worker 用 Celery 的 ping，Redis 用 `redis-cli ping`）。api 與 worker 掛載同一個具名 volume。

### D9：測試策略
預設測試（不需 GPU、Redis、Docker）：以 FastAPI 的測試客戶端搭配注入的假 Job 儲存與假佇列測 API；用記憶體內的假實作與真實的狀態機規則測 Job 轉移與鎖；用合成影格測上傳驗證（含 zip slip、壓縮炸彈、非連續編號、損毀影格）；worker 的處理函式以假偵測器測成功、失敗、重複投遞與終態不覆寫。新增 `integration` 標記（預設排除）：真實 Redis 的轉移與鎖測試，以及透過 compose 的端到端流程；沒有 Docker 或 GPU 時以 skip 處理並說明原因。這與 v0.1 的 `gpu`、`data` 標記一致。

## Risks / Trade-offs

- **上傳是不受信任的輸入（zip slip、壓縮炸彈、巨大檔案）** → 在 D4 逐項擋下，並以針對性的測試覆蓋；上限可設定且有保守的預設值。
- **visibility timeout 與長 Job 的互動** → D5 的任務時間上限小於鎖逾時；超時的 Job 失敗而不是被重複執行。代價是超過上限的超長 Sequence 無法處理，上限需要在文件中明說，且可由環境變數調整。
- **solo pool 只能一次處理一個 Job** → v0.2 的刻意取捨，換取 CUDA 穩定；吞吐量的提升留給 v0.3 的多副本與後續的批次化。
- **Redis 單點** → AOF 加 volume 只降低風險；高可用不在 v0.2 範圍。
- **GPU 容器只能在有 NVIDIA 驅動的機器上驗證** → 預設測試不依賴它；`integration` 標記的端到端測試與 compose 啟動驗證只在這類機器上跑，並在 tasks 中明確列出。
- **影格 zip 要求平鋪且只含影像** → 少數使用者可能習慣包一層資料夾；錯誤訊息要明確指出規則，之後可放寬而不破壞相容性。

## Open Questions

- 解壓上限與任務時間上限的預設值，要依實際 MOT17 序列（最大約 1050 幀、單 Job 約數十秒到數分鐘）量測後再定，數字不影響 spec 與任務拆分。
