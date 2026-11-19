# Design

## Context

動機見 proposal。現況（已在程式中確認）：

- `jobs/layout.py` 的 `StorageLayout(root)` 把路徑算成 `root/jobs/<id>/frames`、`root/jobs/<id>/result.txt`，並有 `staged_frames`（在 `root/staging` 下建暫存）、`publish_frames`（`os.rename` 把暫存搬到 `frames/`）、`write_result`、`remove_job`。
- `app/api.py` 的 `_submit`：`staged_frames` → `unpack_upload` 解開並驗證 → `publish_frames` → `store.create` → `enqueue`；入隊失敗時 `remove_job` 並把 Job 標為 `failed`。`_result_text` 以 `layout.result_path(...).read_text()` 讀結果；`_health_problems` 以在 `layout.root` 建暫存檔檢查可寫入。`create_app(store, layout, limits, enqueue, ping_queue, clock, metrics)`。
- 上傳是 zip，解開後影格都在頂層，檔名是 `<名稱>.jpg|png…`（`unpack_upload` 拒絕子資料夾與不安全的路徑）；所以影格檔名可以直接當作物件鍵的一部分。
- `worker/processing.py` 的 `process_job`：取得鎖 → `RUNNING` → `_run_tracking`，它在 `_within_time_limit` 內以 `layout.frames_dir(job_id)` 與 `layout.result_path(job_id)` 呼叫 `track_sequence`（它同時寫 `tracking.config.metadata_path_for(result)` 也就是 `result.meta.json`）；例外時 `_discard_partial_output` 並把 Job 標為 `failed`，成功時標 `succeeded`。`WorkerContext` 是 frozen dataclass。
- `jobs/settings.py` 的 `Settings` 是 frozen dataclass，新欄位都有預設值；`OPENMOT_STORAGE_DIR` 必須是絕對路徑。
- Compose：api 與 worker 都掛 `storage:/data/storage`；`worker` 掛權重檔（唯讀）；已有 TensorRT 與 monitoring 的 override。Docker daemon 是 sidecar，所以先前的端對端測試不用 bind mount 掛工作目錄路徑，而是用具名 volume 與 tar 串流。Compose 版本 v5.3.1。
- 既有端對端測試（`tests/test_compose_e2e.py`）的模式：專屬專案名稱與埠、缺 Docker、nvidia runtime、權重或影格時 skip、結束時清除。
- `api-requirements.txt` 與 `pyproject.toml` 的 `service` 相依都以下限指定（`celery>=5.4`、`redis>=5`），只有 `prometheus_client` 釘死。系統 Python 已有 boto3 1.43.50；本機 MinIO 映像不存在，需要從網路拉取。

## Goals / Non-Goals

**Goals:**
- 讓 api 與 worker 能在沒有共用磁碟的情況下，用 S3 相容儲存交換影格與結果，且預設行為不變。
- 用真實的 MinIO 證明：同一個序列的 Result 與命令列逐位元組相同。
- 文件把「已驗證」與「沒驗證」切乾淨。

**Non-Goals:**
- Kubernetes 的 S3 overlay、物件的保存期限與生命週期、預簽 URL、伺服器端加密、多部分上傳、把權重或 engine 放進 S3、多個 bucket、靜態金鑰以外的認證。
- 平行上傳與下載；驗證 AWS S3 本身。

## Decisions

### D1：物件儲存是一個小介面，S3 實作接受可注入的客戶端

新增 `jobs/object_store.py`：一個 `ObjectStore` 協定，只有這次用到的操作 `upload_file(key, path)`、`download_file(key, path)`、`read_bytes(key)`、`list_keys(prefix)`、`delete_prefix(prefix)`、`check()`；`S3ObjectStore(client, bucket)` 以 boto3 客戶端實作。`create_object_store(settings)` 才 `import boto3`（延遲匯入，檔案系統後端永遠不需要它），並設定 botocore 的連線與讀取逾時（連線 5 秒、讀取 30 秒，重試沿用 boto3 預設），使提交與健康檢查不會無限期卡住。單元測試用一個實作同樣客戶端方法的記憶體假客戶端，不新增開發相依（不用 moto）。

替代方案：直接在 `app/api.py` 與 `worker/processing.py` 呼叫 boto3。拒絕：測試要 mock boto3 的細節，且後端選擇會散在兩處。

### D2：鍵的配置鏡像檔案系統，本機暫存沿用既有的 `StorageLayout`

鍵是 `jobs/<job_id>/frames/<檔名>`、`jobs/<job_id>/result.txt`、`jobs/<job_id>/result.meta.json`，與檔案系統的相對路徑一致。本機暫存**不新增新的路徑計算**：api 用既有的 `staged_frames`（`<storage_dir>/staging`），worker 用 `StorageLayout(<storage_dir>/scratch)`，於是 `frames_dir`、`result_path`、`remove_job` 全部照用，追蹤流程不知道自己跑在哪個後端。

### D3：`Settings` 新增有預設值的欄位，驗證在啟動時

`storage_backend: str = "filesystem"`、`s3_bucket: str | None`、`s3_endpoint_url: str | None`、`s3_region: str = "us-east-1"`。`load_settings` 驗證：後端必須是 `filesystem` 或 `s3`；`s3` 必須有 bucket；端點必須以 `http://` 或 `https://` 開頭；bucket 名稱不得為空白。金鑰**不**放進 `Settings`（避免被印出或序列化），由 boto3 自己從環境變數讀取。`OPENMOT_STORAGE_DIR` 仍然是必要設定，兩種後端都當作本機暫存，這樣既有的設定檢查與測試不變。

### D4：api 的提交流程：解開到本機暫存，再逐張上傳，失敗就清乾淨

`s3` 後端下，`_submit` 的 `publish_frames`（`os.rename`）換成「逐張 `upload_file`」。上傳在 `staged_frames` 的區塊內完成，區塊結束時本機暫存自動清除。任何一張失敗：`delete_prefix(jobs/<id>/)`、拋 `StorageError`（既有路徑把它轉成 `500` 與「cannot store the upload」）、不建立 Job。入隊失敗時 `remove_job` 的對應動作是 `delete_prefix`。`create_app` 末尾新增有預設值的 `objects: ObjectStore | None = None`，`None` 時整條路徑與現在逐位元組相同。

### D4a：刪除用單一「Job 的物件」操作，而不是呼叫端各自列舉

`delete_prefix` 內部分頁列出並以批次刪除（每批最多 1000 個，這是 S3 `DeleteObjects` 的上限）；任何刪除錯誤都往上拋，不吞掉。清理失敗時，呼叫端在原本的錯誤之外不再拋第二個，但把清理失敗寫進日誌（含 Job ID，不含金鑰），因為「留下孤兒物件」是需要被看見的事。

### D5：worker 的處理流程：下載 → 既有追蹤 → 上傳 → 才標 `succeeded`

`WorkerContext` 末尾新增有預設值的 `objects: ObjectStore | None = None`。`s3` 後端下 `_run_tracking` 在同一個 `_within_time_limit` 區塊內依序：清除殘留暫存、下載 `frames/` 到 `scratch`、`track_sequence`（路徑指向 `scratch`）、上傳 `result.txt` 與 `result.meta.json`；整個區塊結束後（無論成功失敗）`remove_job(scratch_layout, job_id)`。上傳在 `store.transition(SUCCEEDED)` **之前**完成，所以 api 看到 `succeeded` 時物件一定已存在。失敗時除了既有的 `_discard_partial_output`，另對 bucket 的 `result.txt`、`result.meta.json` 盡力刪除（失敗只記日誌），避免失敗的 Job 留下 Result 物件。時限（`OPENMOT_TASK_TIME_LIMIT_SECONDS`）涵蓋下載與上傳，因為它們是 Job 的一部分。

### D6：結果讀取與健康檢查

`s3` 後端下 `_result_text` 以 `read_bytes(jobs/<id>/result.txt)` 解碼為 UTF-8；物件不存在對應既有的 `500`「result ... is missing」。`_health_problems` 在 `s3` 後端多一項 `objects.check()`（`head_bucket`），失敗訊息只含例外類別與 bucket 名稱，**不含**端點之外的設定與任何金鑰；本機暫存可寫入的檢查保留。

### D7：Compose override 用「同一個目標路徑、不同來源」打破共用

`docker-compose.s3.yml` 加入 `minio` 服務（官方 `minio/minio` 映像，釘版本，啟動命令 `server /data`，健康檢查用 `mc ready` 或對 `/minio/health/live` 的 HTTP 檢查，資料放 `minio-data` 具名 volume），並覆寫 api 與 worker：環境變數（後端、端點 `http://minio:9000`、bucket、金鑰）、`depends_on: minio: service_healthy`，以及把 `/data/storage` 的來源分別改成 `api-scratch` 與 `worker-scratch`（Compose 以容器內目標路徑合併 volume，所以同一個 `/data/storage` 會被覆寫成不同來源）。金鑰以 `${MINIO_ROOT_USER:?...}`、`${MINIO_ROOT_PASSWORD:?...}` 引用，缺少時渲染失敗，所以提交的檔案內沒有預設金鑰；api 與 worker 的 `AWS_ACCESS_KEY_ID` 與 `AWS_SECRET_ACCESS_KEY` 直接用同一組值（開發用的 root 金鑰，文件註明正式環境應建立專屬的存取金鑰）。bucket 不由 Compose 建立：端對端測試以 boto3 建立，文件說明手動建法；`/health` 在 bucket 不存在時回報 `503`，這本身就是提示。

### D8：端對端測試用專屬名稱，Result 與命令列比對

新檔 `tests/test_s3_storage_e2e.py`（`integration` 與 `gpu` 標記）：專案 `openmot-s3-e2e`、volume 以 `openmot-s3-e2e-` 開頭、api 埠 18003 與 MinIO 埠 19000（可由環境變數覆寫）、隨機產生的金鑰與 bucket；缺 Docker、nvidia runtime、權重、影格、或 MinIO 映像無法取得時 skip 並說明。流程與既有端對端相同（影格以 tar 串流放進臨時工作區，不 bind mount 工作目錄路徑）。驗證：Job `succeeded`；Result 與中繼檔和「同一個權重、同一組設定以命令列處理同一序列」逐位元組相同；bucket 內 `frames/` 恰有 N 個物件與 `result.txt`、`result.meta.json`；api 與 worker 的 `/data/storage` 來自不同的 volume（`docker inspect`）；兩個本機暫存的 `staging` 與 `scratch` 為空；bucket 不存在時 `/health` 為 `503`。結束時只清除上述名稱的資源。執行前後比對不屬於 `openmot-s3-e2e*` 的容器、卷與映像完全相同（新拉取的 MinIO 映像是唯一允許新增的映像，並記錄它的標籤與大小）。

### D9：相依與映像大小

`boto3` 加進 `service` 與 `api-requirements.txt`（下限 `>=1.34`，與其他服務相依一致）。實作時量測 api 與 worker 映像大小的增量（`docker images` 前後、同一個基底），寫進驗證紀錄；worker 映像已接近 CI runner 的磁碟上限，若增量使 `publish` 有風險就停下回報，不自行降級或移除其他東西。`tests/test_image_imports.py` 既有的規則（映像內程式碼只能匯入被 `COPY` 的套件）不受影響，因為 boto3 是第三方相依，不是專案內套件。

## Risks / Trade-offs

- **逐張上傳／下載的延遲** → 一個 600 張的序列會是 600 次請求；量測並記錄提交時間與 worker 開始處理前的下載時間（本機 MinIO 上的數字，不代表跨網路）；平行化列為後續。
- **半個 Job** → 上傳失敗與入隊失敗都清乾淨（D4、D4a），並以測試固定；清理本身失敗則記日誌，因為不能保證清理一定成功。
- **worker 在處理途中被終止，留下本機暫存** → 下次處理同一個 Job 先清除（D5）；不同 Job 的殘留暫存不會被自動清掉，文件列為限制（暫存只在本機、被 volume 隔離，重新建立容器即清除）。
- **物件儲存與 Job Status 的一致性** → `succeeded` 之前先上傳（D5），所以不會出現 `succeeded` 卻沒有 Result；反過來，上傳成功後標記失敗（Redis 故障）會留下孤兒 Result 物件，這次不處理，文件列為限制。
- **只驗證 MinIO** → 文件寫明沒有在 AWS S3 或其他相容服務上驗證；S3 API 的使用限於 `PutObject`／`GetObject`／`ListObjectsV2`／`DeleteObjects`／`HeadBucket`，降低相容性風險，但不是驗證。
- **開發用的 root 金鑰** → 只在本機 MinIO；提交的檔案內沒有金鑰（D7）。
- **映像變大** → 量測並回報（D9）。
- **Compose volume 合併行為依賴 Compose 版本** → 以渲染測試固定「api 與 worker 的 `/data/storage` 來源不同」，版本改變導致合併行為不同時測試會失敗。

## Migration Plan

無資料遷移：預設後端不變，S3 後端是新增的選項。要回復，還原程式、`docker-compose.s3.yml` 與文件即可；bucket 內的資料屬於使用者，不由專案刪除。
