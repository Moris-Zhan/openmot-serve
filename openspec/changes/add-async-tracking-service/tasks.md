# Tasks

## 1. 專案骨架與設定（tracking-job-api、tracking-job-execution）

- [x] 1.1 在 `pyproject.toml` 加入 `service` extras（FastAPI、uvicorn、python-multipart、Celery、redis client），把 `app*`、`worker*` 納入打包，新增預設排除的 `integration` 測試標記；驗證 `python -m pytest -q` 仍全數通過，且 `python -c "import app, worker"` 可執行
- [x] 1.2 先寫失敗的測試，再實作環境變數設定函式（儲存根目錄、Redis URL、上傳與解壓上限、權重路徑、推論精度、鎖逾時、任務時間上限）；驗證測試涵蓋「全部有預設或必填、缺必填值與非法值都在啟動時失敗並指出變數名稱」
- [x] 1.3 確認 `tracking.sequence` 的 import 鏈是否連帶載入 torch，並依結果決定 API 映像是否需要 torch（必要時把影格檢查拆成不需 torch 的模組）；驗證在不安裝 torch 的虛擬環境中 `import app` 成功，並把決定寫回 design.md 的 D8

## 2. Job 紀錄與狀態機（tracking-job-api、tracking-job-execution）

- [x] 2.1 先寫測試再實作 Job 紀錄與合法轉移規則（`queued → running → succeeded|failed`、`queued → failed`，終態不可覆寫、狀態不倒退）；驗證參數化測試涵蓋所有合法與非法的轉移
- [x] 2.2 先寫測試再實作 Job 儲存的介面與 Redis 版本（原子轉移、失敗原因、未知 ID 回報不存在、每 Job 的鎖含逾時）；驗證預設測試以記憶體內的假實作通過，`-m integration` 對真實 Redis 通過，涵蓋「兩個行程同時搶同一個 Job 只有一個成功」
- [x] 2.3 實作共用儲存的路徑規則與原子寫入（`jobs/<job_id>/frames/`、`result.txt`，影格先在暫存資料夾驗證再 rename）；驗證測試涵蓋路徑不會逃出儲存根目錄、中途失敗不留下半成品

## 3. 上傳驗證（tracking-job-api）

- [x] 3.1 先寫測試再實作 zip 驗證與解壓：串流寫入並限制大小、確認是 zip、拒絕絕對路徑與 `..` 與子資料夾與非影像項目、以宣告與實際位元組雙重限制解壓總量、確認影格編號連續且可解碼；驗證測試涵蓋「非 zip」「沒有影格」「zip slip」「壓縮炸彈」「超過大小上限」「編號不連續」「影格損毀」，且每個失敗情境都沒有留下任何檔案
- [x] 3.2 驗證合法的合成影格 zip 解壓後，影格內容與數量和原本一致，並可直接交給 `tracking.sequence.list_frames`；把驗證規則（平鋪 zip、副檔名、上限）寫進文件的草稿段落，留待 4.4 整理

## 4. HTTP API（tracking-job-api）

- [x] 4.1 先寫測試再實作 `POST /jobs`（以注入的假儲存與假佇列）；驗證測試涵蓋「合法 zip 回 202 與 job ID、Job Status 為 queued」「連續提交得到不同 ID」「不合法上傳回 4xx 且沒有建立 Job」「排入佇列失敗回 5xx 且移除影格」
- [x] 4.2 先寫測試再實作 `GET /jobs/{job_id}` 與 `GET /jobs/{job_id}/result`；驗證測試涵蓋「各個 Job Status 的回應」「failed 帶失敗原因」「未完成與失敗時 Result 回 4xx 且可區分」「未知 ID 回 404」「Result 每行 10 個欄位」
- [x] 4.3 先寫測試再實作 `GET /health`；驗證測試涵蓋「Redis 可連線且儲存可寫入回 200」「Redis 無法連線回 503 並指出佇列後端」「儲存不可寫回 503 並指出儲存」
- [x] 4.4 在 `docs/service.md` 以繁體中文說明 API（請求範例用 curl、回應範例、zip 打包方式、錯誤碼與上限）；驗證文件中的 curl 範例能對本機以假後端啟動的 API 照字面執行

## 5. Worker（tracking-job-execution）

- [x] 5.1 先寫測試再實作 Job 處理函式（取得鎖 → 轉 running → 呼叫 `track_sequence` → 轉 succeeded；任何例外轉 failed 並記錄原因且不留下 Result；終態的 Job 直接略過）；驗證以假偵測器的測試涵蓋「成功」「影格損毀」「偵測丟出例外」「儲存失敗」「重複投遞只處理一次」「終態不被覆寫」「失敗後可繼續處理下一個 Job」
- [x] 5.2 驗證服務路徑與命令列產生相同的 Result：對同一份合成影格與同一個假偵測器，處理函式輸出的 Result 與 `python -m tracking.run` 的輸出逐位元組相同
- [x] 5.3 實作 Celery 應用與任務設定（acks_late、reject_on_worker_lost、prefetch 1、solo pool、任務時間上限小於鎖逾時），並在啟動時先跑環境檢查再載入偵測器一次；驗證測試涵蓋「環境檢查失敗或權重驗證失敗時啟動以非 0 結束且不處理 Job」「連續兩個 Job 只載入一次偵測器」「超過時間上限的 Job 標為 failed」
- [ ] 5.4 在有 GPU 與 Redis 的本機（不經容器）端到端驗證：啟動 worker 與 API，提交一個真實的 MOT17 影格 zip，輪詢到 succeeded，Result 與 `python -m tracking.run` 的結果逐位元組相同；中途終止 worker 後 Job 不會停在 running；把結果與耗時記錄在 docs/service.md

## 6. 容器與 compose（container-deployment）

- [ ] 6.1 撰寫 api 與 worker 的 Dockerfile 與 `.dockerignore`（`python:3.11-slim`、`uv pip install`、worker 含 cu128 的 torch、不含權重與資料）；驗證兩個映像都能建置，並以 `docker run ... ls` 或映像檢查確認其中沒有 `*.pth`、`data/MOT17` 與憑證檔案
- [ ] 6.2 撰寫 `deploy/docker/docker-compose.yml`（redis 含 AOF 與 volume、api、worker 含 GPU 預約、共用具名 volume、權重唯讀 bind mount、Redis 就緒後才啟動、每個服務的健康檢查）；驗證 `docker compose config` 通過，且 `docker compose up` 後三個服務皆為 healthy、`GET /health` 回 200
- [ ] 6.3 驗證 worker 在看不到 GPU 時啟動失敗：以不要求 GPU 的設定啟動 worker，確認它以非 0 結束並輸出 CUDA 不可用，不接受 Job
- [ ] 6.4 以 `integration` 標記寫端到端測試並在本機執行：透過 compose 提交 zip、輪詢到 succeeded、取回 Result；接著重建 api 與 worker 容器，確認先前 Job 的 Job Status 與 Result 仍可取回，並確認 worker 晚於 api 啟動時已排入的 Job 仍被處理；沒有 Docker 或 GPU 時此測試以 skip 說明原因

## 7. 文件與收尾

- [ ] 7.1 在 `docs/service.md` 補上容器部署說明（前置條件含 NVIDIA Container Toolkit 與權重放置、`docker compose up`、環境變數表、卷與資料保存、疑難排解）；驗證文件中的指令照字面執行能啟動並完成一次提交
- [ ] 7.2 更新 README：勾選路線圖 v0.2、把 Status 改成反映 v0.2、API 與部署章節的 _planned_ 標記對應到實際已完成的部分；驗證 README 的端點表與實際 API 一致
- [ ] 7.3 執行預設的完整測試（不含 `gpu`、`data`、`integration`）與 `openspec validate add-async-tracking-service --strict`；驗證兩者皆通過

## Workflow follow-up

- 全部完成後執行 `/opsx:archive`，把三份 delta spec 合併進 `openspec/specs/`
