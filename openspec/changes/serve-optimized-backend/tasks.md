# Tasks

## 1. 設定、中繼檔與 worker 接線（tracking-job-execution、sequence-tracking）

- [x] 1.1 先寫失敗的測試再修改 `tracking/config.py` 與 `tracking/run.py`：`RunMetadata` 新增 `backend: str = "torch"`（驗證後端名稱），`_metadata_document` 只在後端不是 `torch` 時加入 `"backend"`，命令列把 `--backend` 傳入；驗證：新測試涵蓋「`tensorrt`、`onnx` 會記錄後端」與「`torch` 的中繼檔內容與修改前逐位元組相同（以固定的預期文字比對）」，既有使用位置參數建構 `RunMetadata` 的測試不修改且全部通過；`ruff check .` 乾淨
- [x] 1.2 先寫失敗的測試再修改 `jobs/settings.py`：`Settings` 新增 `backend`（預設 `torch`）與 `engine`（預設 `None`），驗證後端名稱、後端與 `OPENMOT_PRECISION` 的組合（沿用 `tracking.config` 的對照表，不 import torch）、`OPENMOT_ENGINE` 必須是絕對路徑且不得與 `torch` 後端並用，錯誤訊息指名相關變數；驗證：新測試涵蓋預設值不變、`onnx` 搭配預設 `fp16` 被拒絕並說明 `onnx` 只支援 `fp32`、不存在的後端列出支援的後端、相對路徑、`torch` 搭配 `OPENMOT_ENGINE`；既有的 `test_settings.py` 不修改且全部通過；api 的測試環境（沒有 torch）可以載入設定
- [x] 1.3 先寫失敗的測試再修改 `worker/run.py` 與 `worker/startup.py`：`default_dependencies(settings)` 以 `functools.partial` 把後端與 `OPENMOT_ENGINE` 綁進載入器，`start_worker` 在 `RunMetadata` 帶入 `settings.backend`，`StartupDependencies` 的簽章不變；`main` 在載入成功後輸出一行偵測器設定（後端、精度、有產物時的路徑，不輸出檔案內容）；驗證：測試以假載入器確認收到 `tensorrt` 與 engine 路徑、載入失敗（包含產物不符與缺少套件的錯誤）時 worker 以結束碼 1 退出且錯誤訊息保留原因、不處理任何 Job，Job 的中繼檔在 `tensorrt` 時含 `backend`、預設時不含；日誌行在 `tensorrt` 時含 engine 路徑、預設時沒有路徑；既有的 worker 測試不修改且全部通過
- [x] 1.4 擴充 `tests/test_service_cli_parity.py`：以假偵測器與 `tensorrt` 後端，驗證服務與命令列產生的 Result 與中繼檔內容完全相同；驗證：新測試通過，且讓其中一邊不記錄後端的突變會失敗

## 2. 映像與 compose（container-deployment）

- [x] 2.1 先寫失敗的測試再重構 `deploy/docker/worker.Dockerfile`：`base` 階段（含 `inference` 套件與 `".[service]"`）、`tensorrt` 階段（再裝 `".[export]"`）、最後的預設階段 `worker`；兩個終端階段都以非 root 使用者執行並設定相同的環境變數，映像不複製任何權重、ONNX 或 engine，`base` 階段建立空的 `/models/engines` 並 `chown` 給執行使用者；驗證：Dockerfile 結構測試通過（預設階段是最後一個階段、沒有 `[export]`，`tensorrt` 階段有），既有的 `test_docker_files.py` 對 `worker.Dockerfile` 的測試不修改且通過；在這台機器上建置兩個 target 成功，預設映像內 `pip list` 沒有 `onnxruntime-gpu` 與 `tensorrt`，`tensorrt` 映像內有固定版本的三個套件並能 `import tensorrt`；`/models/engines` 是空目錄且屬於 `openmot`；以事先 `docker volume create` 的卷掛載後，`openmot` 能寫入（若沒有繼承擁有者，停下來回報並依設計改用 root 的 `engine-builder`，不悄悄改規格）
- [x] 2.2 先寫失敗的測試再新增 `deploy/docker/docker-compose.tensorrt.yml`：`worker` 以 `tensorrt` target 建置、映像 `openmot-worker:tensorrt`、`OPENMOT_BACKEND=tensorrt`、`OPENMOT_PRECISION=fp16`、`OPENMOT_ENGINE=/models/engines/${OPENMOT_ENGINE_FILE:?…}`，外部卷（名稱 `${OPENMOT_ENGINES_VOLUME:-openmot-engines}`）以唯讀掛載；另有 `profiles: ["tools"]` 的 `engine-builder`（同一映像、`entrypoint: python`、可寫掛載、保留 GPU）；驗證：結構測試通過（疊加後 api 與 Redis 的設定與基礎檔相同、worker 的環境變數與掛載符合上述、沒有 bind mount），有 Docker 時 `docker compose config` 成功，沒有設定 `OPENMOT_ENGINE_FILE` 時報錯並說明；既有的 compose 測試不修改且通過
- [x] 2.3 在這台機器上端到端驗證並寫成 `integration`／`gpu` 標記的測試 `tests/test_tensorrt_worker_e2e.py`（缺少 Docker、GPU 或權重卷時略過）：先量測同一個 engine 重複執行的結果是否逐位元組相同（決定比對的措辭）；全程使用獨立的名稱（compose 專案名 `openmot-trt-e2e`、engine 卷 `openmot-trt-e2e-engines`、該專案自己的 Redis 與 storage 卷，`openmot-weights` 只以唯讀沿用），絕不動使用者 `openmot` 專案的容器、卷與映像；以映像內的指令建 ONNX 與 engine 到該 engine 卷；啟動 Redis、api 與 TensorRT worker；提交 MOT17-09 的影格 zip 並等到 `succeeded`；與同一個映像、同一個 engine 的 `python -m tracking.run --backend tensorrt --engine …` 比對 Result 與中繼檔；記錄 Job 耗時；驗證：測試通過，並用錯的 `OPENMOT_ENGINE_FILE`、在預設映像設 `OPENMOT_BACKEND=tensorrt` 兩個情境確認 worker 啟動失敗且錯誤訊息指出原因；結束後只清除名稱以 `openmot-trt-e2e` 開頭的容器與卷並確認 `openmot` 專案的資源（`openmot-weights`、`openmot_storage`、`openmot_redis-data`、`openmot-worker:dev`）仍在

## 3. k8s 設定與文件（kubernetes-deployment、container-deployment）

- [x] 3.1 先寫失敗的測試再修改 `deploy/k8s/base/kustomization.yaml`：ConfigMap 加字面值 `OPENMOT_BACKEND=torch`；驗證：測試確認產生的設定載入後後端是 `torch`、沒有 `OPENMOT_ENGINE`、compose 設定的每個變數仍都在其中且值相同，既有的 `test_k8s_base_config.py` 測試不修改且通過；渲染的 ConfigMap 名稱雜湊改變是預期的，測試不寫死雜湊
- [x] 3.2 更新文件並以測試鎖住：`docs/service.md` 的環境變數表加入 `OPENMOT_BACKEND` 與 `OPENMOT_ENGINE`，新增「TensorRT worker」一節（建立 `openmot-weights` 與 `openmot-engines` 卷、以 `engine-builder` 匯出 ONNX 並建 engine、`OPENMOT_ENGINE_FILE`、啟動、確認、重建時機，並如實標示：engine 綁 GPU 與 TensorRT 版本、`onnx` 比 PyTorch 慢、k8s 沒有 TensorRT 映像、CI 沒有建置 `tensorrt` target、錯誤設定使 api 與 worker 都啟動失敗）；說明啟動日誌那一行的格式；`docs/kubernetes.md` 的設定表加入 `OPENMOT_BACKEND`，並說明套用時 worker 會因 ConfigMap 改變而重建；驗證：文件測試檢查環境變數表與 `Settings` 讀取的變數一致、指令與檔名和 repo 一致、限制都有寫出，沒有把 2.3 沒驗證的部分說成已驗證

## 4. 整體檢查（integration）

- [x] 4.1 執行預設的完整測試、`ruff check .`、`openspec validate serve-optimized-backend --strict` 與 `openspec validate --specs --strict`，並以 `-m "integration or gpu"` 執行 2.3 的端到端測試；驗證：全部通過，預設測試沒有因本變更新增被略過的測試（需要 Docker、GPU 的除外），端到端的結果記錄在 `docs/service.md`

## Workflow follow-up

- 完成後用 `/opsx:archive` 封存這個變更並把 delta spec 同步到四份主規格（`tracking-job-execution` 新增兩條需求、`container-deployment` 新增一條並修改一條；`sequence-tracking` 與 `kubernetes-deployment` 的修改各取代同名需求）。
- 後續：`add-int8-quantization` 之後，才有資料做路線圖 v0.5 的「前後對照」，不在這個變更內。
