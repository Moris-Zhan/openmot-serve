# Proposal

## Why

README 的路線圖把「S3-compatible storage」列在 Later，儲存列也寫著「S3-compatible storage later」。現在 api 與 worker 靠同一個共用檔案系統（`OPENMOT_STORAGE_DIR`，Compose 是具名 volume，k8s 是 PVC）傳遞影格與結果，所以兩者必須掛同一個磁碟，也就必須在同一台機器或有共用儲存。改成 S3 相容的物件儲存後，api 與 worker 可以各自只有本機暫存目錄，影格與結果放在 bucket，兩者就能分開部署。

## What Changes

- 新增 `OPENMOT_STORAGE_BACKEND=filesystem|s3`，**預設 `filesystem`，未設定時行為與現在完全相同**。選 `s3` 時另需 `OPENMOT_S3_BUCKET`，可選 `OPENMOT_S3_ENDPOINT_URL`（MinIO、RustFS 等相容服務）與 `OPENMOT_S3_REGION`；憑證走 boto3 的標準環境變數（`AWS_ACCESS_KEY_ID`、`AWS_SECRET_ACCESS_KEY`），不讀寫進映像或日誌。設定錯誤在啟動時就失敗。
- **S3 後端的資料流**：
  - api 驗證並解開上傳後，把影格上傳到 `jobs/<job_id>/frames/<檔名>`，本機暫存隨即清掉；
  - worker 把該 Job 的影格下載到本機暫存目錄、用既有的追蹤流程處理，再把 `result.txt` 與 `result.meta.json` 上傳到 `jobs/<job_id>/`，最後清掉暫存；
  - api 的 `GET /jobs/{id}/result` 從 bucket 讀取，回應與檔案系統後端相同；
  - `/health` 改為檢查 bucket 可連線，`503` 並指出物件儲存不可用；
  - 入隊失敗時刪除該 Job 已上傳的物件。
- 新增 `boto3` 到 `service` 相依與 api 映像的相依清單。
- 新增 Compose override `deploy/docker/docker-compose.s3.yml`：加入專屬的 S3 相容容器（RustFS），並讓 api 與 worker 各用**不同**的本機暫存 volume，使「沒有共用磁碟」本身成為驗證的一部分。
- 實測：用專屬名稱（`openmot-s3-e2e*`）的 RustFS 容器與 Compose 專案，跑一個真實的 Job，驗證 Result 與命令列逐位元組相同、bucket 內的物件符合預期、api 與 worker 沒有共用磁碟、暫存目錄事後是空的。
- 更新 `docs/service.md`（環境變數與 S3 啟動方式）與 README（路線圖勾選、儲存列），並寫明限制。

## Capabilities

### New Capabilities

- `object-storage`: 可選的 S3 相容儲存後端：設定與驗證、影格上傳、worker 的下載與結果上傳、結果讀取、健康檢查、清理。

### Modified Capabilities

- `container-deployment`: 新增 S3 的 Compose override，以專屬的 RustFS 容器與互不共用的暫存 volume 啟動整個服務。

## Impact

- 程式：新增 `jobs/object_store.py`（物件儲存介面、S3 實作、可注入的客戶端）；`jobs/settings.py`（新設定，新欄位都有預設值）；`app/api.py`（上傳、讀結果、健康檢查、清理；`create_app` 末尾新增有預設值的參數）；`worker/processing.py`、`worker/startup.py`、`worker/run.py`、`app/run.py`（接上後端）。檔案系統後端的程式路徑不改。
- 相依與映像：`pyproject.toml` 的 `service` 與 `deploy/docker/api-requirements.txt` 加 `boto3`。這會讓 api 與 worker 映像變大；worker 映像本來就接近 CI runner 的磁碟上限（見 `docs/ci.md`），所以要量測並記錄增量，過大就回報而不是硬推。
- 測試：設定、物件儲存、api、worker 的單元測試（用可注入的假客戶端，不新增開發相依）；Compose override 的渲染測試；`gpu` 與 `integration` 標記的 RustFS 端對端測試，缺 Docker、GPU、權重、影格或映像時 skip 並說明。
- 文件：`docs/service.md`、README。
- 不改：Kubernetes 清單（仍用 PVC，文件註明不支援 S3 後端）、追蹤與偵測流程、API 的路由與回應格式、`default` 的檔案系統後端行為。
- 範圍外：Kubernetes 的 S3 overlay；物件的保存期限與生命週期；預簽 URL、伺服器端加密、多部分上傳；把權重或 engine 放進 S3；多個 bucket；除了靜態金鑰以外的認證方式。
- 資源安全：端對端只使用 `openmot-s3-e2e*` 的容器、卷與埠；`openmot-weights` 只以唯讀使用；不動 `openmot` 專案的資源。
- 風險：
  - 一個 Job 有數百張影格，逐張上傳與下載會增加提交與開始處理的延遲；量測並記錄實際數字，這次不做平行上傳。
  - 檔案系統後端的「一個資料夾」是原子的，物件儲存不是：上傳到一半失敗時，必須清掉已上傳的物件，不能留下半個 Job。
  - 官方 `minio/minio` 映像拉不到，測試伺服器改用 RustFS（`rustfs/rustfs:1.0.6`，Apache 2.0，專案自己發佈）；它與 MinIO 不是同一個程式，S3 行為差異只能靠實測確認。
  - 只驗證 RustFS 的釘定版本 `1.0.6`，沒有驗證 MinIO、AWS S3 或其他相容服務；文件如實寫明。
  - 以靜態金鑰認證；本機 RustFS 的金鑰由環境變數提供，不寫進任何提交的檔案。
