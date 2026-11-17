# Design

## Context

動機見 proposal.md。目前的事實：

- `tracking.run.load_default_detect_frame(weights, precision, device, backend="torch", engine=None)` 已支援三個後端（上一個變更完成），且 `torch` 路徑沒有改變。worker 以 3 個參數的 `StartupDependencies.load_detect_frame` 呼叫它（`worker/startup.py`），測試用的假載入器也是 3 個參數。
- `jobs/settings.py` 的 `Settings` 同時被 api 與 worker 載入；api 映像沒有 torch，所以設定的驗證不能 import `tracking.detector`（它 import torch）。後端與精度的對照表在不依賴 torch 的 `tracking/config.py`（`SUPPORTED_PRECISIONS_BY_BACKEND`）。
- `RunMetadata` 只有兩個正式的建構位置：`worker/startup.py` 與 `tracking/run.py`；它寫出的中繼檔由 `_metadata_document` 組成，沒有後端欄位。`eval` 的輸出另有 `summary.json`，不經過 `RunMetadata`。
- `worker.Dockerfile` 是單一階段：以 root 安裝 `uv`、torch 與 `".[service]"`，最後切到使用者 `openmot`。映像不含 `inference` 套件，也沒有 `[export]` extra（`onnx==1.17.0`、`onnxruntime-gpu==1.22.0`、`tensorrt-cu12==10.13.3.9`）。
- `inference.backends.DEFAULT_DIRECTORY` 是「套件所在位置的 `models/engines`」：在以非可編輯模式安裝進映像的套件裡，這個位置是 `site-packages` 底下，不是我們會掛載的目錄，所以容器內必須用明確的產物路徑。
- 開發環境的 Docker daemon 是側車：bind mount 看不到本機路徑，所以權重已有 `docker-compose.weights-volume.yml` 用具名的外部卷 `openmot-weights` 提供。
- 上一個變更的實測：TensorRT FP16 與 PyTorch FP16 的偵測數量可能略有差異（30 幀裡 2 幀），TensorRT 重建 engine 也可能與前一次不同；同一個 engine 重複執行是否逐位元組相同尚未在服務層面驗證。

## Goals / Non-Goals

**Goals:**
- 預設行為完全不變：沒有新環境變數時，設定、worker、映像、中繼檔、k8s 清單的行為與內容都與現在相同（中繼檔與 `torch` 輸出逐位元組相同）。
- 選用 TensorRT 時，錯誤的設定（組合、產物、版本、GPU）在 worker 啟動時就失敗並指名原因，而不是在處理第一個 Job 時才失敗。
- 在這台機器上能實際驗證：建置映像、在映像內建 engine、跑完整的 Job 並與同一個 engine 的命令列結果比對。

**Non-Goals:**
- 不做 k8s 的 TensorRT 映像或 overlay、INT8、執行期切換後端、API Job 回應的後端欄位、中繼檔裡的產物雜湊、後端的 Prometheus 指標、CI 建置 `tensorrt` target。
- 不調整 `onnx` 後端的效能（它比 PyTorch 慢，只是因為規格允許而可用）。

## Decisions

**D1：把後端與產物路徑在 `default_dependencies(settings)` 以 `functools.partial` 綁進載入器，`StartupDependencies` 與 `start_worker` 的簽章不變。**
`load_default_detect_frame` 已有 `backend` 與 `engine` 參數；`worker.run.main` 在載入 `Settings` 之後才建立相依，所以可以把它們綁定。替代方案：把 `StartupDependencies.load_detect_frame` 改成 5 個參數——所有測試的假載入器都要改，而且改動沒有帶來更多保證。`start_worker` 仍在 `RunMetadata` 裡帶入 `settings.backend`。

**D2：設定的驗證放在 `jobs/settings.py`，用 `tracking.config` 的對照表，不依賴 torch。**
`OPENMOT_BACKEND` 必須在 `SUPPORTED_BACKENDS` 內；組合不合法時以 `SettingsError` 失敗，訊息指名兩個變數與該後端支援的精度（例如 `OPENMOT_BACKEND 'onnx' does not support OPENMOT_PRECISION 'fp16', it supports ('fp32',)`）。`SettingsError` 讓 worker 以結束碼 2 退出，發生在載入偵測器之前；api 共用同一份設定，所以同一份錯誤設定也會讓 api 啟動失敗——與現在無效的 `OPENMOT_PRECISION` 的行為一致。沒有隱含推斷：`OPENMOT_BACKEND=tensorrt` 但精度仍是預設 `fp16` 剛好合法；`onnx` 必須同時明確設定 `OPENMOT_PRECISION=fp32`。
`OPENMOT_ENGINE` 是選用路徑，必須是絕對路徑（與 `OPENMOT_STORAGE_DIR` 的規則一致，避免依賴工作目錄）；只在後端不是 `torch` 時有意義，`torch` 後端設了它時以 `SettingsError` 拒絕，避免使用者以為它生效。

**D3：`RunMetadata` 新增 `backend: str = "torch"`，只有不是 `torch` 時才寫入中繼檔。**
預設值讓所有既有的位置參數建構式與測試不變；`_metadata_document` 在 `backend != "torch"` 時才加 `"backend"` 鍵，所以 `torch` 的輸出逐位元組不變（現有的比對與 k8s e2e 都不受影響）。`tracking/run.py` 與 `worker/startup.py` 傳入實際的後端，命令列與服務共用同一份記錄邏輯，「同後端時結果相同」才成立。`RunMetadata` 驗證後端名稱，與驗證精度一致。

**D4：worker 映像用同一個 Dockerfile 的 `tensorrt` target，預設 target 在檔案最後。**
結構：`base` 階段（到 `uv pip install ".[service]"` 為止，另外複製 `inference` 套件）、`tensorrt` 階段（`FROM base`，再 `uv pip install --system ".[export]"`）、最後的預設階段 `worker`（`FROM base`，設定使用者與環境變數）。`tensorrt` 階段同樣補上使用者與環境變數。`base` 階段另外 `mkdir -p /models/engines && chown openmot /models/engines`：這個空目錄讓新建的具名卷在第一次掛載時繼承擁有者（Docker 會把映像該路徑的內容與權限複製進空的具名卷，包含事先以 `docker volume create` 建好的卷），使 `engine-builder` 與 worker 都能以非 root 的 `openmot` 執行；目錄是空的，不違反「映像不含 engine」。實作時要驗證預先建好的外部卷確實繼承擁有者，若不成立就改讓 `engine-builder` 以 root 執行並回頭修規格，不悄悄改變「所有容器非 root」的現有規格。因為預設 target 是最後一個階段，沒有 `--target` 的 `docker build` 與 compose 的預設行為不變；`inference` 套件是不含重依賴的純 Python，預設映像也複製它，這樣在預設映像上設 `OPENMOT_BACKEND=tensorrt` 得到的是 `BackendError`（指名缺少的套件與 `[export]` extra），而不是難懂的 `No module named 'inference'`。替代方案：獨立的 Dockerfile——兩份檔案重複大部分內容，TensorRT 與 torch 的 CUDA 版本固定得同步改兩處。

**D5：engine 用具名的外部卷 `openmot-engines`，由映像內的指令建置，worker 以唯讀掛載。**
新增 `docker-compose.tensorrt.yml`：`worker` 以 `tensorrt` target 建置、映像名 `openmot-worker:tensorrt`、`OPENMOT_BACKEND=tensorrt`、`OPENMOT_PRECISION=fp16`、`OPENMOT_ENGINE=/models/engines/${OPENMOT_ENGINE_FILE:?…}`、卷 `engines`（名稱 `${OPENMOT_ENGINES_VOLUME:-openmot-engines}`，預設是 `openmot-engines`，可覆寫以便測試使用獨立的卷）唯讀掛在 `/models/engines`；另有一個 `profiles: ["tools"]` 的 `engine-builder` 服務，使用同一個映像、`entrypoint: python`、可寫地掛載同一個卷與權重、保留 GPU，讓 `python -m inference.export_onnx` 與 `python -m inference.build_engine --output-dir /models/engines` 在 TensorRT 版本與執行時相同的環境裡執行。與權重的作法一致（具名外部卷，在側車 daemon 上也可用）；不使用 bind mount。engine 檔名帶有權重雜湊、TensorRT 版本與 GPU 型號，所以由使用者以 `OPENMOT_ENGINE_FILE` 指定；沒有設定時 compose 以必填變數的語法報錯並說明。替代方案：新增 `OPENMOT_ENGINE_DIR` 讓 worker 以命名慣例找檔——多一個設定、多一個規格項目，目前的需求不需要。

**D6：k8s 只在 ConfigMap 加 `OPENMOT_BACKEND=torch`。**
與 `OPENMOT_PRECISION` 同樣的慣例（明確的預設值）。既有的對照測試只要求 compose 設定的變數都在 ConfigMap 裡，並且載入後等於程式預設值；新增的字面值等於預設值，所以通過；`OPENMOT_ENGINE` 不進預設設定。ConfigMap 內容改變會讓名稱的雜湊改變並觸發 Pod 滾動——這是既有的設計，文件要提醒套用時 worker 會重建。

**D7：worker 啟動時輸出一行偵測器設定。**
`worker/run.py` 目前只在錯誤時輸出，換成 TensorRT 後從外面看不出一個 worker 用哪個後端。啟動載入偵測器成功後輸出一行到標準輸出，例如 `detector: backend=tensorrt precision=fp16 engine=/models/engines/…`；只記後端、精度與產物路徑（`torch` 後端沒有 engine 時省略該欄位），不輸出檔案內容或雜湊。不新增 Prometheus 指標：它會連動 `observability` 的「儀表板只顯示服務實際輸出的指標」規格與 dashboard 測試，超出這次範圍。

**D8：驗證在服務層面以映像內的命令列為基準，並與使用者的 `openmot` 資源隔離。**
預設測試涵蓋設定、啟動、中繼檔、Dockerfile 與 compose 的結構、k8s 對照；需要 GPU、TensorRT 與 Docker 的驗證另以 `gpu` 與 `integration` 標記。與命令列比對時，基準必須是**同一個 engine**：TensorRT 重建 engine 可能產生不同的 tactic 而使偵測略有差異，所以比對的是「容器內的 worker」與「同一個容器映像、同一個 engine 的 `python -m tracking.run --backend tensorrt --engine …`」，不是與主機上另外建的 engine 比。
這台機器的 Docker daemon 上已有使用者的 `openmot` 專案資源（外部卷 `openmot-weights`、`openmot_storage`、`openmot_redis-data`、映像 `openmot-worker:dev`）。端到端測試使用獨立的名稱：compose 專案名 `openmot-trt-e2e`、engine 卷 `openmot-trt-e2e-engines`（以 `OPENMOT_ENGINES_VOLUME` 指定）、該專案自己的 Redis 與 storage 卷；`openmot-weights` 只以唯讀沿用，不修改內容。測試結束只移除名稱以 `openmot-trt-e2e` 開頭的容器與卷，絕不動 `openmot` 專案的資源。CI 不建置 `tensorrt` target（GitHub 託管 runner 沒有 GPU，建置要下載數 GB 的套件且無法執行），CI 只跑結構測試；建置與端到端只在有 GPU 的機器上驗證，文件如實標示。

## Risks / Trade-offs

- [在預設映像設 `OPENMOT_BACKEND=tensorrt` 會在啟動時才失敗] → `BackendError` 指名缺少的套件與 `[export]` extra，文件指向 `tensorrt` target；設定驗證不依賴映像內容，所以不會假裝能擋下。
- [engine 與 GPU、TensorRT 版本綁定，換機器或升級映像後 worker 啟動失敗] → 這是 sidecar 驗證的設計目的；錯誤訊息指名不符的項目並提示重建；文件寫明重建流程。
- [同一個 engine 的結果是否逐位元組穩定未知] → 任務裡先量測同一個 engine 重複執行的結果是否相同；若不相同，與命令列的比對改為容差內一致並如實記錄，不把它寫成逐位元組相同。
- [具名卷沒有繼承 `/models/engines` 的擁有者，`engine-builder` 寫入失敗] → 實作時先驗證（D4）；失敗時 `engine-builder` 改以 root 執行並修規格，worker 仍以非 root 唯讀掛載。
- [端到端測試誤動使用者的 `openmot` 專案資源] → 獨立的專案名與卷名（D8），清理只比對 `openmot-trt-e2e` 前綴，`openmot-weights` 只唯讀。
- [`onnx` 後端比 PyTorch 慢] → 文件如實標示（約 21.8 FPS 對 55 FPS）與用途（驗證與可攜性，不是加速）。
- [api 與 worker 共用設定，錯誤的後端設定也讓 api 啟動失敗] → 與現有 `OPENMOT_PRECISION` 的行為一致，視為一致的 fail-fast；文件說明。
- [中繼檔只在非 torch 時多一個欄位，讀取端若假設鍵固定會受影響] → 目前沒有讀取端依賴固定的鍵（命令列與服務只寫入）；欄位只增不改。

## Migration Plan

沒有資料遷移。對既有的 compose 或叢集：不設新變數就沒有任何變化（k8s 的 ConfigMap 多一個字面值會使名稱雜湊改變並重建 Pod，與任何設定變更相同）。要啟用 TensorRT：建 `openmot-engines` 卷與 `openmot-weights` 卷、以 `tensorrt` 映像建 ONNX 與 engine、以 override 啟動。回復：不疊加 override 並重建 worker。

## Open Questions

- 同一個 engine 重複執行的結果是否逐位元組相同：實作的第一步量測，結果決定比對的措辭，不改變範圍或任務。
