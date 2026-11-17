## Why

v0.5 的前一個變更讓偵測器可以匯出成 ONNX 與 TensorRT，並在命令列用 `--backend` 選用：TensorRT FP16 在 RTX 5090 上推論約 4.8 ms（PyTorch FP16 約 11.2 ms）、端到端約 91 FPS（PyTorch 約 55 FPS），HOTA 與 PyTorch 相當。但服務還跑不到這個速度：Celery worker 只讀 `OPENMOT_WEIGHTS` 與 `OPENMOT_PRECISION`，永遠用 PyTorch 後端；worker 映像也沒有 TensorRT 與 ONNX Runtime。路線圖 v0.5 的「前後對照」與 INT8 都要在服務層面有可切換的後端才有意義。

## What Changes

- worker 新增 `OPENMOT_BACKEND`（`torch`、`onnx`、`tensorrt`，預設 `torch`）與選用的 `OPENMOT_ENGINE`（明確的 ONNX 或 engine 檔路徑）。未設定時行為與現在完全相同。後端與 `OPENMOT_PRECISION` 的組合沿用 `inference-backends` 的限制（`tensorrt` 只支援 `fp16`、`onnx` 只支援 `fp32`），不合法的組合在載入偵測器之前就讓 worker 啟動失敗並指名變數；沒有隱含的精度推論。
- 產物不存在、sidecar 驗證失敗（權重、GPU 型號、TensorRT 版本不符）或所需套件缺少時，worker 啟動失敗並說明原因，不處理任何 Job。偵測器仍在啟動時載入一次。
- worker 啟動時輸出一行偵測器設定（後端、精度、產物路徑；不輸出任何檔案內容），讓維運者從日誌看出一個 worker 用哪個後端；不新增 Prometheus 指標。
- 結果的中繼檔在後端不是 `torch` 時多記錄 `backend` 欄位（命令列與服務共用同一份記錄邏輯）；`torch` 的輸出逐位元組不變。
- `worker.Dockerfile` 新增 `tensorrt` build target（多裝固定版本的 `[export]` extra 與 `inference` 套件）；預設 target 產生的映像不變。新增 compose override：以該 target 建置、設定 `OPENMOT_BACKEND=tensorrt` 與 `OPENMOT_PRECISION=fp16`、以唯讀掛載 engine 所在的目錄。映像只建立一個空的、屬於非 root 使用者的 `/models/engines`，讓新建的具名卷第一次掛載時繼承擁有者；engine 與 ONNX 檔不進映像，用映像內的 `python -m inference.export_onnx` 與 `python -m inference.build_engine` 離線建到掛載的目錄，確保 TensorRT 版本與執行時一致。
- k8s 的 ConfigMap 加上明確的預設值 `OPENMOT_BACKEND=torch`（與 `OPENMOT_PRECISION` 同樣的慣例）；`OPENMOT_ENGINE` 是選用的，不放進預設設定。
- 文件與測試：`docs/service.md` 的環境變數表、TensorRT worker 的建置與啟動手冊，以及 `docs/kubernetes.md` 的設定表。
- 不包含：CI 建置 `tensorrt` target（GitHub 託管 runner 沒有 GPU，且要下載數 GB 套件；CI 只跑結構測試）、k8s 的 TensorRT overlay 或映像（沒有叢集無法驗證）、INT8、worker 在執行期切換後端、把推論後端寫進 API 的 Job 回應、在中繼檔記錄產物的雜湊。

## Capabilities

### New Capabilities

（無。）

### Modified Capabilities
- `tracking-job-execution`: 新增「worker 依設定選用偵測後端」的需求（預設不變、組合與產物的驗證在啟動時、與命令列同後端時結果相同）。
- `sequence-tracking`: 「Tracking parameters are configurable and recorded」在後端不是 `torch` 時另記錄後端，`torch` 的中繼檔維持不變。
- `container-deployment`: 「Images do not contain secrets, weights or data」納入 ONNX 檔與 engine；新增 TensorRT worker 的 build target 與 compose override 的需求。
- `kubernetes-deployment`: 「Configuration names and defaults match the compose deployment」納入 `OPENMOT_BACKEND` 的預設值。

## Impact

- 程式碼：`jobs/settings.py`（`backend`、`engine` 與組合驗證）、`worker/run.py`、`worker/startup.py`（把後端與產物路徑帶進載入器、中繼檔記錄後端）、`tracking/config.py`（`RunMetadata` 與中繼檔）、`tracking/run.py`（命令列寫入同一份中繼檔）。
- 部署：`deploy/docker/worker.Dockerfile`（新 target、複製 `inference` 套件）、新的 compose override、`deploy/k8s/base/kustomization.yaml`（ConfigMap 字面值）。
- 測試：設定、啟動、中繼檔、命令列與服務的一致性、Dockerfile 與 compose、ConfigMap 與 compose 的對照；需要 GPU、TensorRT 與 Docker 的驗證以 `gpu`／`integration` 標記，預設測試不依賴它們。
- 文件：`docs/service.md`、`docs/kubernetes.md`、`docs/evaluation.md`（連結）、README 路線圖不勾選（INT8 與前後對照仍未做）。
- 風險：engine 與 GPU 型號、TensorRT 版本綁定，換 GPU 或映像版本需要重建；`onnx` 後端雖然可用，速度比 PyTorch 慢（約 21.8 FPS 對 55 FPS），文件要明說。
