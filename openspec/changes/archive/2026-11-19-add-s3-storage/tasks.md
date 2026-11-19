# Tasks

## 1. 設定

- [x] 1.1 先寫失敗的測試（新檔 `tests/test_settings_storage_backend.py`）：未設定時 `storage_backend` 為 `filesystem` 且其餘欄位與現在相同；`s3` 搭配 bucket 可載入，端點與區域可選（區域預設 `us-east-1`）；`s3` 沒有 bucket、bucket 為空白、端點不是 `http://` 或 `https://`、後端值不支援，都拋 `SettingsError` 並指名設定；`Settings` 的字串表示與錯誤訊息不含金鑰。驗證：新測試先失敗；既有的 `tests/test_settings*.py` 不修改。
- [x] 1.2 實作：`jobs/settings.py` 新增有預設值的欄位與驗證，`OPENMOT_STORAGE_DIR` 的檢查不變。驗證：1.1 通過，既有測試不需修改就通過。

## 2. 物件儲存介面與 S3 實作

- [x] 2.1 先寫失敗的測試（新檔 `tests/test_object_store.py`，含一個記憶體假客戶端）：`upload_file`／`download_file`／`read_bytes` 的往返內容相同；`list_keys` 與 `delete_prefix` 處理超過一頁與超過 1000 個物件；`delete_prefix` 的刪除錯誤往上拋；`check()` 在 bucket 不存在或不可連線時拋出且訊息只含例外類別與 bucket 名稱，不含金鑰；讀取不存在的鍵拋出可辨識的例外；`create_object_store` 在檔案系統後端不匯入 boto3。驗證：新測試先失敗。
- [x] 2.2 實作 `jobs/object_store.py`（協定、`S3ObjectStore`、`create_object_store`，連線 5 秒與讀取 30 秒逾時）。驗證：2.1 通過，ruff 乾淨。

## 3. api 接上 S3 後端

- [x] 3.1 先寫失敗的測試（新檔 `tests/test_api_object_storage.py`，用假客戶端）：提交後 `jobs/<id>/frames/` 下恰有 N 個物件、本機 `staging` 為空；上傳到第 K 張失敗時回應 `5xx`、bucket 無該 Job 的物件、沒有建立 Job；入隊失敗時回應 `503`、物件被刪除、Job 為 `failed`；結果從 bucket 讀取且回應與檔案系統後端相同；已 `succeeded` 卻沒有 Result 物件時回應 `500` 並指出遺失；`/health` 在 bucket 可連線時 `200`、不可連線或不存在時 `503` 並指出物件儲存不可用且不含金鑰；未傳入物件儲存時（預設）所有行為與現在相同。驗證：新測試先失敗；既有的 api 測試不修改。
- [x] 3.2 實作：`create_app` 末尾新增有預設值的 `objects`，`_submit`、`_result_text`、`_health_problems` 依後端分支；清理失敗記日誌；`app/run.py` 在 `s3` 後端建立物件儲存。驗證：3.1 通過，既有測試不需修改就通過。

## 4. worker 接上 S3 後端

- [x] 4.1 先寫失敗的測試（新檔 `tests/test_worker_object_storage.py`）：Job 處理後 bucket 有 `result.txt` 與 `result.meta.json`、本機暫存為空、Job 為 `succeeded`；上傳在標為 `succeeded` 之前完成（以記錄呼叫順序驗證）；同一個序列與設定下，S3 的 Result 與中繼檔和檔案系統後端逐位元組相同；下載失敗時 Job 為 `failed` 並記錄原因、bucket 無 Result 物件、worker 繼續處理下一個 Job；追蹤失敗時暫存被清除、bucket 無 Result 物件；同一個 Job 殘留的暫存在下載前被清除且不影響結果；時限涵蓋下載；`frames_processed` 指標在 `s3` 後端數 bucket 內的影格數；`start_worker` 在 `s3` 後端帶入物件儲存與 `<storage_dir>/scratch`、檔案系統後端維持原樣；`WorkerContext` 不傳 `objects` 時行為與現在相同。驗證：新測試先失敗；既有的 worker 測試不修改。
- [x] 4.2 實作：`WorkerContext` 末尾新增 `objects`，`_run_tracking` 的 `s3` 分支（D5），`worker/startup.py`（`StartupDependencies` 新增有預設值的 `make_objects`，所以 `worker/run.py` 不需修改）在 `s3` 後端建立物件儲存並用 `StorageLayout(<storage_dir>/scratch)`；`worker/celery_app.py` 的影格計數改數 bucket。驗證：4.1 通過，既有測試不需修改就通過，ruff 乾淨。

## 5. 相依與映像

- [x] 5.1 先寫失敗的測試（加在新檔 `tests/test_object_storage_dependency.py`）：`pyproject.toml` 的 `service` 與 `deploy/docker/api-requirements.txt` 都含 `boto3`；兩份清單的 `boto3` 下限一致。驗證：新測試先失敗；既有的相依與映像測試（含 `tests/test_image_imports.py`）不修改。
- [x] 5.2 把 `boto3>=1.34` 加進兩份清單；建置 api 與 worker 映像並記錄大小增量（前後各一次 `docker images`，同一基底）。增量使 worker 映像逼近 CI runner 的磁碟上限時停下回報，不自行處理。驗證：映像可建置、大小數字記在驗證紀錄；預設測試通過。

## 6. Compose override

- [x] 6.1 先寫失敗的測試（新檔 `tests/test_compose_s3.py`，需 `docker compose config`，無 Docker 時 skip）：不含 override 的渲染結果與現在相同（api 與 worker 仍共用 `storage`）；含 override 時 api 與 worker 的 `/data/storage` 來源是兩個不同的具名 volume、都不是 S3 伺服器的資料 volume；api 與 worker 的後端、端點、bucket 與金鑰環境變數正確；缺少 `S3_ACCESS_KEY` 或 `S3_SECRET_KEY` 時渲染失敗並指名；`s3` 服務有健康檢查，api 與 worker 依賴它就緒；檔案內沒有任何預設金鑰字串。驗證：新測試先失敗。
- [x] 6.2 新增 `deploy/docker/docker-compose.s3.yml`（D7）。驗證：6.1 通過；既有的 `tests/test_compose*.py` 不修改。

## 7. 真實 RustFS 端對端

- [x] 7.1 新增 `tests/test_s3_storage_e2e.py`（`integration` 與 `gpu` 標記，D8）：專屬名稱（專案 `openmot-s3-e2e`、卷以 `openmot-s3-e2e-` 開頭、api 埠 18003、S3 伺服器埠 19000，可由環境變數覆寫）、隨機金鑰與 bucket、`openmot-weights` 唯讀；缺 Docker、nvidia runtime、權重、影格或 RustFS 映像無法取得時 skip 並說明原因。驗證：缺條件時整份 skip 並印出原因。
- [x] 7.2 測試內容：Job `succeeded`；Result 與中繼檔和同一組設定以命令列處理同一序列逐位元組相同；bucket 內 `frames/` 恰有 N 個物件與 `result.txt`、`result.meta.json`；api 與 worker 的 `/data/storage` 來自不同的 volume；兩邊本機暫存的 `staging` 與 `scratch` 為空；bucket 不存在時 `/health` 為 `503`。驗證：在這台機器上 `pytest -m "integration or gpu" tests/test_s3_storage_e2e.py -s` 全部通過；記下提交到 `succeeded` 的時間與日誌的關鍵行。
- [x] 7.3 確認沒有動到其他專案的資源：執行前後比對 `docker ps -a`、`docker volume ls`、`docker images` 中不屬於 `openmot-s3-e2e*` 的項目完全相同，新拉取的 RustFS 映像是唯一允許新增的映像並記下標籤與大小；測試結束後專屬名稱的容器與卷已清除。驗證：前後清單的差異貼在驗證紀錄。
- [x] 7.4 若 RustFS 映像拉不到、GPU 不可用或其他條件使端對端無法執行，如實記錄並回報使用者決定，不編造結果，不改用其他方式宣稱已驗證。驗證：驗證紀錄寫明實際結果。

## 8. 文件與文件測試

- [x] 8.1 先寫失敗的文件測試（新檔 `tests/test_object_storage_docs.py`）：`docs/service.md` 有 `OPENMOT_STORAGE_BACKEND`、`OPENMOT_S3_BUCKET`、`OPENMOT_S3_ENDPOINT_URL`、`OPENMOT_S3_REGION` 的說明與 S3 override 的啟動指令、bucket 的建法；文件列出限制（只驗證 RustFS 的釘定版本 1.0.6（不是 MinIO）、沒有 k8s 支援、沒有物件保存期限、逐張傳輸的延遲數字、孤兒物件與殘留暫存）；README 的路線圖已勾選 S3 並指向文件、儲存列不再寫「S3-compatible storage later」。驗證：新測試先失敗。
- [x] 8.2 更新 `docs/service.md` 與 README，數字取自 7.2 與 5.2 的紀錄。驗證：8.1 通過；`tests/test_service_docs.py`、`tests/test_readme_*.py` 不需修改就通過，有因事實改變而必須更新的斷言才改並在驗證紀錄說明。

## 9. 收尾

- [x] 9.1 `pytest` 預設全部通過、`ruff check` 乾淨、新檔案 `ruff format --check` 乾淨、`openspec validate add-s3-storage --strict` 通過。驗證：貼出各指令的結尾輸出。
- [x] 9.2 驗證紀錄逐項寫明「已驗證」與「沒驗證」（尤其：只驗證 RustFS、沒有 k8s、沒有平行傳輸、沒有物件保存期限、孤兒物件與殘留暫存、映像大小增量）。驗證：文件與最後的回報一致。

## Workflow follow-up

- 提交並推送分支（規劃文件先提交一次，實作完成後再提交），合併到 main 後以 `/opsx:archive` 封存並同步主規格。
