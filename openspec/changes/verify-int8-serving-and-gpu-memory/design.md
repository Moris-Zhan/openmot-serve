# Design

## Context

動機見 proposal。現況：

- `eval/latency.py` 的 `Dependencies` 以位置參數建構（`check_environment, load_staged, clock, resolve_artifact`），測試用同樣的方式建立；`tests/test_latency_cli.py` 斷言 `latency.json` 的 `results` 鍵恰好是 `{inference, end_to_end, throughput_fps}`。
- `deploy/docker/docker-compose.tensorrt.yml` 把 worker 的 `OPENMOT_PRECISION` 寫死為 `fp16`；`tests/test_compose_tensorrt.py` 在清掉 `OPENMOT_*` 環境變數後渲染，斷言它是 `fp16`。
- 產物驗證（`inference/artifacts.py` 的 `verify_sidecar`）已比對 sidecar 的 `precision`，所以精度與 engine 不符本來就會被擋下，不需要新程式。
- 既有的 `tests/test_tensorrt_worker_e2e.py` 在模組內用固定名稱（專案 `openmot-trt-e2e`、卷 `openmot-trt-e2e-*`、埠 18001），並在卷內以 `engine-builder` 建 FP16 engine。
- host 的 `models/engines/` 已有 INT8 engine 與 sidecar（TensorRT 10.13.3.9、RTX 5090）；映像 `openmot-worker:tensorrt` 已存在，但它是在 INT8 變更之前建的，裡面的 `tracking.run` 只接受 `fp16`、`fp32`，worker 也會拒絕 `int8`。所以端對端測試必須每次先 `compose build worker`（靠快取很快），不能只在映像不存在時才建；既有的 FP16 測試本來就是這樣做的。
- 這台機器 GPU 閒置（1 MiB 已用），`nvidia-smi --query-gpu=index,memory.used` 可用。

## Goals / Non-Goals

**Goals:**
- 用一個可重現的端對端測試證明：同一個 INT8 engine，經 worker 服務與經命令列的結果逐位元組相同。
- 讓延遲量測的結果帶 GPU 記憶體，量法對所有後端一致，並且能從結果看出 GPU 當時是否閒置。
- 文件把「已驗證」與「沒驗證」切乾淨，不留過時的敘述。

**Non-Goals:**
- 在映像內重建 INT8 engine（需要掛載 MOT17 校準影格）；只驗證 host 建的 engine 能被 worker 服務。
- Kubernetes 的 TensorRT 映像與 INT8 服務；k8s 仍是 PyTorch。
- 每個程序各自的記憶體歸屬、記憶體峰值的取樣；只取三個時間點的整張 GPU 讀數。
- 調整任何準確度的結論；INT8 仍是「不建議」。

## Decisions

### D1：GPU 記憶體以 `nvidia-smi` 讀整張 GPU 的已用量，三個時間點

讀 `nvidia-smi --query-gpu=memory.used`（指定裝置的索引，單位 MiB），在 `execute()` 的最開頭（還沒做環境檢查、還沒碰 CUDA）、偵測器載入完成後、量測結束後各讀一次，記錄兩個增量。第一次讀數在這個程序使用 CUDA 之前，所以增量包含程序自己的 CUDA context、引擎或模型、配置器的快取，是「服務這個偵測器大約要占多少 GPU 記憶體」，而不是權重的大小。

替代方案：
- `torch.cuda.mem_get_info()`：不需要外部指令，但第一次呼叫就會建立 CUDA context，使基準值已含 context，增量偏小，且與 TensorRT 的配置不同源。
- `torch.cuda.max_memory_allocated()`：只計 PyTorch 配置器，TensorRT 與 ONNX Runtime 自己配置的記憶體量不到，三種後端無法比較。
- `pynvml`：精度相同，但多一個依賴；容器內的 PID 對映也不可靠。

限制照實記錄：整張 GPU 的讀數會被其他程序污染（所以記錄載入前的讀數）；只取三個時間點，載入過程的瞬間峰值取不到。

### D2：記憶體放在結果檔的獨立頂層鍵，不放進 `results`

`latency.json` 新增頂層鍵 `gpu_memory`，內容是三個讀數、兩個增量與量法說明；不可用時是 `{"available": false}`。不動 `results` 的三個鍵，既有測試與已記錄的結果檔的讀法都不變。人類可讀的表格在記憶體可用時才多一行增量，不可用時不顯示。

### D3：探針以相依注入，預設為「不可用」

`Dependencies` 在最後加一個有預設值的欄位 `read_gpu_memory`，預設永遠回報不可用；`default_dependencies()` 才接上真正的 `nvidia-smi` 探針。這樣既有的單元測試（位置參數建構）不需要改，也不會因為跑在有 GPU 的機器上而得到不同的輸出。探針失敗（找不到 `nvidia-smi`、輸出無法解析、裝置不是 CUDA）一律回報不可用，量測照常完成。

### D4：compose override 只改一行

`OPENMOT_PRECISION: ${OPENMOT_PRECISION:-fp16}`。未設定時渲染結果與現在相同，所以 `tests/test_compose_tensorrt.py` 中渲染層級的斷言不用改；但 `test_the_worker_builds_the_tensorrt_target_and_selects_the_backend` 讀的是未插值的 YAML 原文，期望值必須改成 `${OPENMOT_PRECISION:-fp16}`，這是新設計該有的一處改動，不是放寬斷言。另外新增測試覆蓋設定 `int8` 的渲染。

### D5：INT8 端對端測試是獨立的模組，用專屬名稱，把 host 的 engine 複製進卷

新增 `tests/test_tensorrt_int8_worker_e2e.py`，不修改既有的 FP16 端對端測試。專屬名稱：compose 專案 `openmot-trt-e2e-int8`、engine 卷 `openmot-trt-e2e-int8-engines`、影格卷 `openmot-trt-e2e-int8-frames`、API 埠 18002（可由環境變數覆寫）。engine 來源是 host 的 `models/engines/` 下已建好的 INT8 engine 與 sidecar：以映像內的非 root 使用者、用 tar 串流把它們複製進新建的卷（與既有測試填影格卷的做法相同；不用 bind mount，因為 Docker daemon 不一定看得到工作目錄的路徑），不重建；host 沒有 INT8 engine 時 skip，訊息含建置指令。通用的小工具（`_docker`、`_text`、`_wait_until`、常數）從既有模組匯入，與卷名稱綁定的 `_compose`、影格填充、命令列基準各自寫一份，不重構既有檔案。

驗證內容：
1. worker 以 `OPENMOT_PRECISION=int8` 啟動後健康，Job 進入 `succeeded`；
2. Result 與中繼檔與「同一個 engine 以命令列處理同一序列」逐位元組相同，中繼檔記錄 `int8` 與 `tensorrt`；
3. worker 日誌含 `backend=tensorrt precision=int8` 與 engine 路徑；
4. 未指定精度（預設 `fp16`）卻掛載 INT8 engine 時，worker 在處理任何 Job 前以非 0 結束，訊息指名精度不符。這是 spec 裡「精度與 engine 不符」的對稱案例，成本最低（不需要第二個 engine），走的是同一個 `precision` 比對。

結束時只清除上述名稱的資源；`openmot-weights` 只以唯讀掛載。

### D6：量測流程與數字的來源

在閒置的 GPU 上，對 PyTorch FP16、TensorRT FP16、TensorRT INT8 各以預設的預熱與量測張數量兩次，執行名稱 `lat-mem-<後端>-<精度>-<序號>`，存在被忽略的 `data/results/latency/`。README 的記憶體欄取這三組各兩次的「量測結束後的增量」範圍；延遲欄沿用已記錄的 `lat-*` 數字（同樣的方法，同樣的 GPU），不用這批新跑的延遲，避免同一個設定出現兩組延遲數字；新批次的延遲若與舊批次差超過一般的跑間差異，照實寫在文件裡。README 另補一列 PyTorch FP16，延遲取自 `lat-torch-fp16-1/2`。

### D7：文件與測試跟著事實更新

`docs/evaluation.md` 的 INT8 一節現在寫著「服務端對端未驗證」「GPU 記憶體未量測」，`tests/test_int8_docs.py` 斷言了這些字樣。事實改變後，文件與測試一起更新：這是這個變更該做的，不是為了過測試而改。新的數字由文件測試對照記錄的結果檔驗證（沿用 `test_int8_docs.py` 的做法，結果檔不在時 skip）。

### D8：映像內的 engine 建置不能依賴 `eval` 套件（驗證時發現，納入這個變更）

為了讓 INT8 端對端測試用目前的程式碼，測試重建了 `openmot-worker:tensorrt`，才發現映像內 `python -m inference.build_engine` 在 import 階段就失敗：`inference/calibration.py` 的 `from eval.split import split_sequence`，而 `worker.Dockerfile` 只 `COPY` 了 `tracking`、`inference`、`jobs`、`metrics`、`app`、`worker`，沒有 `eval`（`pyproject.toml` 的打包清單有，Dockerfile 沒有）。這是上一個 INT8 變更引入的，連 FP16 的建置都受影響，既有的 FP16 端對端測試因此在 fixture 就失敗；現有規格本來就要求映像內含建置 engine 所需的程式，所以這是違反既有規格，不需要新需求。

決定：把匯入延後到 `_tuning_pool` 內（只有真的要選校準影格時才需要 `eval.split`）。映像內建 INT8 engine 本來就不支援，因為校準需要 MOT17 影格，所以行為不變，也不改 Dockerfile 與映像內容。並加兩個回歸測試：封鎖 `eval` 後仍可 import 建置模組；掃描會進映像的套件的專案內匯入，都被 Dockerfile 的 `COPY` 涵蓋，這樣下次有人從映像內的程式碼匯入映像沒有的套件時，在單元測試就會被擋下，不必等到重建映像。

替代方案：在 Dockerfile 加 `COPY eval ./eval`——會把含 TrackEval vendor 的整個套件放進 worker 映像，映像變大且內容的規格邊界要重新確認；而且只修這一個匯入，不防止同類問題。

## Risks / Trade-offs

- **GPU 上有其他程序** → 記錄載入前的讀數；量測前用 `nvidia-smi` 確認閒置；文件註明只在閒置的 GPU 上有意義。
- **映像內的 TensorRT 版本與 host 建 engine 的版本不同** → 載入驗證會擋下並指名版本，那就是要記錄的結果；不繞過驗證，也不在映像內重建。
- **整張 GPU 的讀數含無關的配置** → 是刻意的（要回答的是服務所需），在結果與文件裡說明量的是什麼；不拿它當模型大小。
- **只取三個時間點** → 瞬間峰值（例如載入 engine 時）取不到；文件列為限制。
- **端對端測試耗時與占用 GPU** → 標記 `gpu` 與 `integration`，預設不跑；用專屬埠與專案名稱，可與 FP16 測試共存。
- **`nvidia-smi` 的輸出格式因驅動版本而異** → 只解析 `--format=csv,noheader,nounits` 的單一整數；解析失敗視為不可用。

## Migration Plan

無資料遷移。compose 的改動向後相容（未設定時行為不變）。要回復，還原該行與新增的檔案即可；結果資料都在被忽略的目錄。
