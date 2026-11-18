# Tasks

## 1. GPU 記憶體探針與延遲量測結果

- [x] 1.1 先寫失敗的測試 `tests/test_latency_memory.py`：`nvidia-smi` 輸出（單一整數、含換行）解析成 MiB；找不到指令、非 0 結束碼、輸出無法解析、裝置是 `cpu` 時回報不可用；`cuda:1` 查詢的是索引 1。驗證：`pytest tests/test_latency_memory.py` 因為探針尚不存在而失敗。
- [x] 1.2 在 `eval/latency.py` 實作探針（以 `nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits -i <索引>` 讀取，失敗一律回報不可用，不拋例外）。驗證：1.1 的測試全部通過。
- [x] 1.3 先寫失敗的測試：`execute()` 依序在環境檢查之前、載入之後、量測結束之後各讀一次；`latency.json` 的頂層 `gpu_memory` 含三個讀數、兩個增量（等於相減）與量法說明，`results` 的鍵仍恰為 `inference`、`end_to_end`、`throughput_fps`；表格在可用時多一行增量、不可用時沒有；不可用時 `gpu_memory` 為 `{"available": false}` 且量測照常完成；載入失敗時不寫任何結果檔。驗證：新測試先失敗。
- [x] 1.4 實作：`Dependencies` 末尾新增有預設值的 `read_gpu_memory`（預設回報不可用），`execute()` 取三個讀數並寫入結果與表格，`default_dependencies()` 接上真正的探針。驗證：1.3 的測試通過，且既有的 `tests/test_latency.py`、`tests/test_latency_cli.py` 不需修改就全部通過。
- [x] 1.5 新增 `gpu` 標記的測試 `tests/test_latency_memory_gpu.py`：在真實 GPU 上以短量測（少量張數）跑 `python -m eval.latency`，結果檔的載入後增量為正、量測結束後的增量不小於 0；缺 CUDA、MOT17 或權重時 skip 並說明。驗證：`pytest -m gpu tests/test_latency_memory_gpu.py -s` 在這台機器通過，並印出實際的增量。

## 2. compose 的精度選項

- [x] 2.1 先寫失敗的測試（加在新檔 `tests/test_compose_tensorrt_precision.py`，不改既有的 `tests/test_compose_tensorrt.py`）：渲染時未設定 `OPENMOT_PRECISION` 得到 `fp16`、設定 `int8` 得到 `int8`，且兩種情況下 api 與 Redis 的設定與 worker 的其他項目相同。驗證：設定 `int8` 的案例先失敗。
- [x] 2.2 將 `deploy/docker/docker-compose.tensorrt.yml` 的 `OPENMOT_PRECISION` 改為 `${OPENMOT_PRECISION:-fp16}`，並在檔頭註解補上如何指定。驗證：2.1 通過；既有的 `tests/test_compose_tensorrt.py` 中渲染層級的測試不需修改就通過，只有讀 YAML 原文的 `test_the_worker_builds_the_tensorrt_target_and_selects_the_backend` 把 `OPENMOT_PRECISION` 的期望值改為 `${OPENMOT_PRECISION:-fp16}`（design D4），並在驗證紀錄說明。

## 3. INT8 的服務端對端驗證

- [x] 3.1 新增 `tests/test_tensorrt_int8_worker_e2e.py`（`gpu` 與 `integration` 標記），依 design D5：專屬名稱（專案 `openmot-trt-e2e-int8`、engine 卷與影格卷以 `openmot-trt-e2e-int8-` 開頭、埠 18002）、把 host 的 INT8 engine 與 sidecar 複製進卷、`openmot-weights` 唯讀；缺 Docker、nvidia runtime、權重卷、MOT17 影格或 host 的 INT8 engine 時 skip，訊息含建置指令。驗證：缺條件時整份 skip 並印出原因。
- [x] 3.2 測試內容：worker 以 `int8` 啟動後健康且 Job `succeeded`；Result 與中繼檔與同一 engine 的命令列結果逐位元組相同，中繼檔記錄 `int8` 與 `tensorrt`；worker 日誌含 `backend=tensorrt precision=int8` 與 engine 路徑；未指定精度卻掛載 INT8 engine 時 worker 在處理任何 Job 前以非 0 結束並指名精度不符。驗證：在這台機器上 `pytest -m "integration or gpu" tests/test_tensorrt_int8_worker_e2e.py -s` 全部通過，並記下 Job 耗時與日誌的關鍵行。
- [x] 3.3 確認沒有動到其他專案的資源：執行前後比對 `docker ps -a`、`docker volume ls`、`docker images` 中不屬於 `openmot-trt-e2e*` 的項目完全相同，且測試結束後 `openmot-trt-e2e-int8*` 的容器與卷已清除。驗證：前後清單的差異只有專屬名稱的項目，結果貼在驗證紀錄。
- [x] 3.4 若映像內的 TensorRT 版本與 host 的 engine 不同導致載入被拒，如實記錄訊息與結論，不繞過驗證、不在映像內重建；此時 3.2 的成功案例標為未達成並回報，由使用者決定下一步。驗證：驗證紀錄寫明實際結果。

## 4. 量測 GPU 記憶體

- [x] 4.1 量測前確認 GPU 閒置（`nvidia-smi` 無其他程序、已用記憶體接近 0），不閒置就停止並回報。驗證：記下量測前的 `nvidia-smi` 輸出。
- [x] 4.2 在 `python -m eval.latency` 以預設張數對 PyTorch FP16、TensorRT FP16、TensorRT INT8 各量兩次，執行名稱 `lat-mem-<後端>-<精度>-<序號>`。驗證：六份 `latency.json` 都有可用的 `gpu_memory`，載入前的讀數接近 0，且 `git status` 沒有新的未追蹤資料檔。
- [x] 4.3 整理三組的載入後與量測結束後增量（MiB 與 GB）與跑間差異；新批次的延遲若與已記錄的 `lat-*` 差超過一般的跑間差異就照實記錄。驗證：數字列在驗證紀錄，可由結果檔重現。

## 5. 文件與文件測試

- [x] 5.1 先更新 `tests/test_int8_docs.py`：移除「服務端對端未驗證」「GPU 記憶體」「未量測」這幾個將不再成立的必備字樣，改成斷言新的事實（INT8 服務端對端已驗證與其限制、記憶體的量法與數字對照記錄的結果檔、README 結果表三列與記憶體欄、`docs/service.md` 不再有「`tensorrt` 只支援 `fp16`」）。這是事實改變後該改的測試，在驗證紀錄中說明。驗證：新斷言在文件更新前先失敗。
- [x] 5.2 更新文件：README 的結果表（補 PyTorch FP16 一列、三列填入記憶體、移除兩個 TBD 與「未量測」、延遲欄沿用記錄的數字）與 v0.5 路線圖的敘述；`docs/service.md` 修正精度敘述並補上以 INT8 服務的啟動方式與驗證結果；`docs/evaluation.md` 的 INT8 一節更新為實測結果，並列出剩下的限制（記憶體是整張 GPU 的三個時間點讀數、只驗證 host 建的 engine、k8s 沒有 TensorRT 映像）。驗證：5.1 的測試通過，文件中沒有與事實矛盾的舊敘述。
- [x] 5.3 確認 `tests/test_latency_docs.py`、`tests/test_service_docs.py`、`tests/test_readme_*.py` 在文件更新後仍通過；有因事實改變而必須更新的斷言才改，並在驗證紀錄說明。驗證：這些測試全部通過。

## 6. 修正映像內 engine 建置的匯入

這一組是在驗證時發現並經使用者決定納入的：worker 映像裡沒有 `eval` 套件，但 `inference/calibration.py` 在 import 階段就匯入 `eval.split`，使映像內的 `python -m inference.build_engine` 連 FP16 都失敗（design D8）。

- [x] 6.1 先寫失敗的測試 `tests/test_image_imports.py`：(a) 在子行程中封鎖 `eval` 套件（把 `sys.modules["eval"]` 設為 `None`）後，仍可 import `inference.build_engine` 與 `inference.calibration`；(b) 以 `ast` 掃描會進 worker 映像的套件（`tracking`、`inference`、`jobs`、`metrics`、`app`、`worker`）的所有 import，凡是匯入專案內其他頂層套件的，該套件必須被 `deploy/docker/worker.Dockerfile` 的 `COPY` 涵蓋。驗證：兩個測試因 `inference/calibration.py` 匯入 `eval.split` 而失敗，且失敗訊息指名 `eval`。
- [x] 6.2 修正：`inference/calibration.py` 把 `from eval.split import split_sequence` 移到 `_tuning_pool` 內才匯入。映像內建 INT8 engine 本來就不支援（需要 MOT17 與 `eval`），行為不變。驗證：6.1 的測試通過，既有的校準與 INT8 建置測試（`tests/test_calibration_selection.py`、`tests/test_build_engine_int8.py`、`tests/test_int8_calibrator.py`）不需修改就通過，ruff 乾淨。

## 7. 收尾

- [x] 7.1 `pytest` 預設全部通過（沒有新的 skip 以外的失敗）、`ruff check` 與 `ruff format --check` 乾淨、`openspec validate verify-int8-serving-and-gpu-memory --strict` 通過。驗證：貼出各指令的結尾輸出。
- [x] 7.2 `gpu` 與 `integration` 測試在這台機器通過：`pytest -m "integration or gpu" tests/test_tensorrt_int8_worker_e2e.py tests/test_latency_memory_gpu.py -s`；既有的 `tests/test_tensorrt_worker_e2e.py` 不受影響（同樣的指令加上它）。驗證：貼出輸出。
- [x] 7.3 驗證紀錄逐項寫明「已驗證」與「沒驗證」（尤其：映像內重建 INT8 engine、k8s、記憶體峰值、GPU 上有其他程序時的數字）。驗證：文件與最後的回報一致。

## Workflow follow-up

- 提交並推送分支（規劃文件先提交一次，實作完成後再提交），合併到 main 後以 `/opsx:archive` 封存並同步主規格。
