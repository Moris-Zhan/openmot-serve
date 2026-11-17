# Proposal

## Why

路線圖 v0.5 的核心問題是：把 YOLOX-X 從 PyTorch 換成 ONNX 與 TensorRT，延遲能降多少、HOTA 掉多少。前一個 change 已經量出 PyTorch FP16 的 baseline（純推論 11.25 ms、端到端 18.2 ms、約 54.6 FPS，HOTA 50.78），也讓偵測快取與分數摘要記錄了後端，但目前 `SUPPORTED_BACKENDS` 只有 `torch`，沒有任何其他後端可比。這個 change 補上 ONNX Runtime 與 TensorRT 兩個後端與它們的匯出、建置流程，並用同一套 `eval.run`、`eval.latency` 量出差異，讓下一個 INT8 change 有可以對照的 FP16 引擎。

在規劃階段已先在這台 RTX 5090 上實測：TensorRT 10.13.3.9 可以建出 FP16 engine（195 MiB），onnxruntime-gpu 最新版 1.32.0 需要 CUDA 13 而無法使用，必須固定在 1.22.0。這些版本限制是這個 change 的前提。

## What Changes

- 新增 `inference/` 套件，內含兩個後端（ONNX Runtime、TensorRT）、匯出與建置指令，以及產物的命名與驗證邏輯。後端只實作推論階段，前處理與後處理（letterbox、解碼後的人物篩選與 NMS）重用 PyTorch 後端的，讓不同後端之間只有推論引擎不同。
- 新增 `python -m inference.export_onnx`：載入 YOLOX-X 權重，匯出固定 `1×3×800×1440`、FP32、opset 17 的 ONNX，並以 `onnx.checker` 檢查。
- 新增 `python -m inference.build_engine`：由 ONNX 建出 TensorRT FP16 engine（workspace 8 GiB、timing cache 與 engine 放在一起）。
- 每個產物旁寫一份 sidecar JSON，記錄權重與 ONNX 的 sha256、輸入尺寸與建置參數；載入時逐項比對，不符就指出是哪一項。
- `SUPPORTED_BACKENDS` 擴充為 `torch`、`onnx`、`tensorrt`；`DetectorSettings` 驗證後端與精度的組合：torch 可用 fp16 與 fp32，onnx 只有 fp32，tensorrt 只有 fp16，其他組合明確報錯。
- `load_default_detect_frame` 與 `DetectorLoader` 在最後加入預設 `torch` 的 `backend` 與 `engine` 參數；`eval.run`、`eval.latency`、`tracking.run` 新增 `--backend` 與 `--engine` 旗標，預設行為不變。
- 新增 `[export]` extra，固定 `onnx==1.17.0`、`onnxruntime-gpu==1.22.0`、`tensorrt-cu12==10.13.3.9`；預設安裝、預設測試與 GitHub CI 不需要它們。
- 在 `docs/evaluation.md` 加入各後端的 HOTA、延遲與吞吐對照表（附 run name），更新 README 路線圖。
- 不包含：worker 的 `OPENMOT_BACKEND`、啟動檢查與 Docker build target（屬後續的 `serve-optimized-backend`）；INT8 量化（屬 `add-int8-quantization`）；動態輸入尺寸、多 batch、CPU 推論；在 GitHub CI 執行 GPU 測試。

## Capabilities

### New Capabilities
- `inference-backends`: 把偵測網路匯出成 ONNX 與 TensorRT 產物、驗證產物與目前環境相符，並以與 PyTorch 後端一致的前後處理執行推論。

### Modified Capabilities
- `benchmark-evaluation`: Benchmark Run 可選擇推論後端與產物，分數摘要記錄實際使用的產物；不支援的後端與精度組合在載入模型前就被拒絕。
- `inference-benchmark`: 延遲量測可指定推論後端，結果記錄所用產物的識別資訊與相關函式庫版本。

## Impact

- **新增檔案**：`inference/`（`artifacts.py`、`backends.py`、`export_onnx.py`、`build_engine.py`、`onnx_backend.py`、`tensorrt_backend.py`）及對應測試；`docs/evaluation.md` 新章節。
- **修改檔案**：`tracking/config.py`、`tracking/detector.py`（後端與精度組合驗證）、`tracking/run.py`、`eval/run.py`、`eval/latency.py`（`--backend`、`--engine`）、`pyproject.toml`（`[export]` extra 與套件清單）、`.gitignore`、`README.md`。
- **依賴**：`onnx`、`onnxruntime-gpu`、`tensorrt-cu12`，只在 `[export]` extra；預設環境不變。
- **風險**：TensorRT 與 onnxruntime 版本與 CUDA 12.8、Blackwell（sm_120）綁定得很緊，升級任一者都要重測；engine 只能在建置它的 GPU 與 TensorRT 版本上使用，故檔名與 sidecar 都記錄這兩項；onnxruntime 與 torch 預設都可能以 TF32 做卷積，ONNX 與 PyTorch 的 FP32 差距要在真實影格上確認；GPU 測試不在 GitHub CI 執行，只能在有 GPU 的機器上用 `pytest -m "gpu and data"` 驗證，文件會寫明。
