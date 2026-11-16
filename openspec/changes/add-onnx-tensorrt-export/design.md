# Design

## Context

動機與範圍見 proposal.md。現況（皆已讀過程式確認）：

- `tracking/detector.py` 已有 `StagedDetector(prepare, infer, finish, synchronize)`；`infer` 回傳解碼後的 `23625×85` 張量（YOLOX head 的解碼在模型內），`finish` 做人物篩選、NMS 與還原座標。新後端只要提供另一個 `infer`。
- `SUPPORTED_BACKENDS = ("torch",)` 在 `tracking/config.py`；`DetectorSettings` 驗證後端與精度，但不驗證兩者的組合。`CacheKey` 與 `summary.json` 已含後端。
- `load_default_detect_frame(weights, precision, device)` 與 `DetectorLoader = Callable[[Path, str, str], ...]` 是 `eval.run`、`tracking.run`、worker 共用的載入介面；`eval/latency.py` 自己有 `_load_torch_staged`。`eval.run` 以「第一次需要偵測才載入模型」的方式延遲載入，所以快取全命中時根本不會載入模型。
- 規劃階段在這台 RTX 5090（driver 580.126.20，torch 2.7.1+cu128，Python 3.11）的暫存虛擬環境實測：
  - legacy TorchScript 匯出（`dynamo=False`、opset 17、`1×3×800×1440`）2.5 秒完成，ONNX 378 MiB。
  - `tensorrt-cu12==10.13.3.9`：由該 ONNX 以 FP16 旗標、8 GiB workspace 建出 195 MiB engine；engine 的輸入與輸出張量都是 FP32；以 torch tensor 的 device pointer 加 `execute_async_v3` 可直接執行，不需要 pycuda。
  - `onnxruntime-gpu==1.32.0`（PyPI 最新版）需要 CUDA 13，載入 CUDA 執行提供者失敗；`session.get_providers()` 只剩 `CPUExecutionProvider`，也就是**請求 CUDA 失敗時 onnxruntime 不會報錯而是悄悄退回 CPU**。`onnxruntime-gpu==1.22.0` 在同一環境載入 CUDA 執行提供者成功。
  - 兩者在隨機雜訊輸入上與 PyTorch FP32 的最大差距分別約 8.5（ONNX）與 17.5（TensorRT FP16），輸出數值最大約 1436；雜訊不代表真實影像，只作為「兩條路徑都能算出接近的結果」的證據，驗收一律在真實影格上做。

## Goals / Non-Goals

**Goals:**
- 用同一套 `eval.run` 與 `eval.latency` 比較 PyTorch、ONNX Runtime、TensorRT 三個後端，且不同後端之間只有推論引擎不同。
- 產物（ONNX、engine）可重現地建置，載入時能證明它與目前的權重、函式庫與 GPU 相符。
- 預設環境、預設測試與 GitHub CI 完全不受新依賴影響。

**Non-Goals:**
- 不做 worker 的 `OPENMOT_BACKEND`、啟動檢查與 Docker build target（`serve-optimized-backend`）。
- 不做 INT8 與校正資料流程（`add-int8-quantization`）。
- 不做動態輸入尺寸、多 batch、CPU 推論，不把 letterbox、解碼或 NMS 放進 ONNX 或 engine。
- 不在 GitHub CI 執行任何需要 GPU 的測試。

## Decisions

### 1. 新增 `inference/` 套件，所有 Python 程式都在其中

```
inference/
  artifacts.py          產物命名、sidecar 讀寫與驗證（不匯入 onnx、onnxruntime、tensorrt）
  backends.py           依後端名稱組出 StagedDetector 的分派
  export_onnx.py        python -m inference.export_onnx
  build_engine.py       python -m inference.build_engine
  onnx_backend.py       ONNX Runtime 的 infer
  tensorrt_backend.py   TensorRT 的 infer
```

`scripts/` 只放 `.sh`，所以匯出與建置指令都是 `python -m inference.<name>`，與既有的 `python -m eval.run`、`python -m tracking.run` 一致。`pyproject.toml` 的套件清單加入 `inference*`。

- **為何**：與專案既有的指令形式一致；測試時可以不載入任何重依賴就匯入 `inference.artifacts` 與 `inference.backends`。
- **替代方案**：放進 `tracking/`（會讓預設匯入路徑碰到重依賴的符號，且 `tracking` 的職責是追蹤）；放進 `scripts/`（使用者明確要求 `scripts/` 只放 shell 腳本）。

### 2. 後端只替換 `infer`

`inference/backends.py` 的 `load_staged_detector(backend, weights, settings, device, engine)` 先取得 PyTorch 後端的 `prepare`、`finish`、`synchronize`，再把 `infer` 換成對應後端的實作。為了讓所有入口都用同一種方式把階段組成 `DetectFrame`，在 `tracking/detector.py` 新增 `run_staged(staged, image)`，`detect` 與新的載入路徑都呼叫它。

- 推論階段的輸入是 `PreparedFrame.batch`（GPU 上的 `1×3×800×1440` FP32 張量），輸出是 GPU 上的 `23625×85` FP32 張量，與 PyTorch 後端的 `infer` 回傳相同。因此 `finish` 的人物篩選、NMS 與還原座標對三個後端完全相同。
- **為何**：這是讓延遲與 HOTA 的差異只能歸因於推論引擎的最直接做法，也沿用了前一個 change 建好的階段接縫。
- `torch_staged_detector` 需要一個 `Detector`（含模型）才能建立；ONNX 與 TensorRT 路徑不應為了取得 `prepare` 與 `finish` 而載入 PyTorch 模型（約 750 MB 與數秒）。實作時把 `prepare`、`finish`、`synchronize` 抽成只依賴 `DetectorSettings` 與 `device` 的函式（`torch_staged_detector` 改為呼叫它們），行為與輸出不變，由既有的階段測試把關。

### 3. 後端與精度的組合由資料表驗證

`tracking/config.py` 以 `SUPPORTED_PRECISIONS_BY_BACKEND = {"torch": ("fp16", "fp32"), "onnx": ("fp32",), "tensorrt": ("fp16",)}` 取代 `SUPPORTED_BACKENDS` 的獨立常數（`SUPPORTED_BACKENDS` 改為其鍵的 tuple，保持既有匯入可用）。`DetectorSettings.__post_init__` 先檢查後端，再檢查精度屬於該後端的清單，錯誤訊息列出該後端支援的精度。

- **為何**：組合規則是資料，不是 if 串；之後 INT8 只需在表中加一列。
- **替代方案**：讓 `--backend onnx` 自動把精度改成 `fp32`。使用者已決定不做隱性推斷：結果檔記錄的精度必須是使用者明確指定的，所以 `--backend onnx` 要同時給 `--precision fp32`，否則報錯並提示正確的精度。
- 這項檢查發生在 `DetectorSettings` 建立時，而 `eval.run`、`eval.latency` 與 `tracking.run` 都在載入模型之前就建立它，所以「載入前拒絕」自然成立。

### 4. 產物命名與 sidecar

位置預設為 `models/engines/`（新增到 `.gitignore`；`*.onnx` 與 `*.engine` 已被忽略，但 sidecar 與 timing cache 不是）。

| 產物 | 檔名 |
|---|---|
| ONNX | `yolox_x-<weights sha8>-800x1440.onnx` |
| ONNX sidecar | `yolox_x-<weights sha8>-800x1440.onnx.json` |
| engine | `yolox_x-<weights sha8>-800x1440-trt<TensorRT 版本>-<GPU slug>-fp16.engine` |
| engine sidecar | `<engine 檔名>.json` |
| timing cache | `<engine 檔名>.timing` |

GPU slug 由 `torch.cuda.get_device_name` 轉小寫並把非英數字元換成 `-`（例如 `nvidia-geforce-rtx-5090`）。

sidecar 欄位（皆為 JSON，鍵排序、結尾換行，與專案其他 JSON 一致）：

- ONNX：`weights_sha256`、`onnx_sha256`、`input_height`、`input_width`、`opset`、`exporter`（`torchscript`）、`torch_version`、`precision`（`fp32`）。
- engine：上列再加 `tensorrt_version`、`gpu_name`、`precision`（`fp16`）、`workspace_bytes`、`optimization_level`、`builder_flags`、`timing_cache`（檔名）。

載入驗證由 `inference/artifacts.py` 的純函式完成：輸入「sidecar 的內容」與「目前環境的觀測值」，回傳不符項目的清單；有任何不符就丟出 `ArtifactError`，訊息格式為 `<項目> mismatch: sidecar has <A>, current is <B>`。權重 sha256 取自既有的 `WEIGHTS_SHA256`／檔案雜湊；ONNX sha256 則重新計算檔案雜湊（378 MiB，約 1 秒內），避免只信任 sidecar。

- **為何**：engine 只能在建置它的 TensorRT 版本與 GPU 上使用，載入時才發現是最糟的失敗點；把版本與 GPU 放進檔名可避免互相覆蓋，放進 sidecar 可避免被改名繞過。
- **替代方案**：只靠檔名比對（被改名或複製即失效）；只靠 TensorRT 的反序列化錯誤（訊息不明確，且 ONNX 路徑沒有類似保護）。

### 5. 在建立結果資料夾之前先解析並驗證產物

`inference/backends.py` 提供 `resolve_artifact(backend, weights_sha256, settings, device, engine)`：依命名規則（或 `--engine` 明確路徑）找出產物，讀 sidecar 並驗證，回傳 `ArtifactInfo`（路徑、sidecar 內容、函式庫版本）。它**不匯入** `onnxruntime` 或 `tensorrt` 的執行階段（TensorRT 版本以 `importlib.metadata` 讀取，GPU 名稱以 torch 讀取），所以很快，也可以在無 GPU 的測試中以假環境測試。

`eval.run` 與 `eval.latency` 在建立結果資料夾與載入模型之前呼叫它；失敗即以非 0 結束碼結束。通過後才建立資料夾，並把 `ArtifactInfo` 寫入 `summary.json` 與 `latency.json`。真正的模型載入維持 `eval.run` 既有的延遲載入。

- **為何**：`eval.run` 在偵測快取全命中時不會載入模型；若驗證綁在載入上，過期的 engine 在快取全命中時不會被發現，摘要也無法記錄產物識別。先解析再延遲載入，兩個問題都解決。
- 缺產物時的錯誤訊息固定格式：`missing <路徑>; build it with: python -m inference.export_onnx` 或 `python -m inference.build_engine`（附上所需參數）。

### 6. 匯出：legacy TorchScript、固定形狀、FP32

`python -m inference.export_onnx`：驗證權重（沿用 `load_yolox_x` 的 sha256 檢查）→ 以 `torch.onnx.export(model, 1×3×800×1440 張量, opset_version=17, dynamo=False, input_names=["images"], output_names=["output"])` 匯出到暫存檔 → `onnx.checker.check_model` → 計算 sha256 → 寫 sidecar → 原子地改名為目標檔。目標檔已存在即拒絕。模型以 FP32 匯出，不做任何圖形簡化。

- **為何選 legacy exporter**：已在本機實測可用且 2.5 秒完成；torch 2.7.1 的 dynamo 匯出路徑對這個模型沒有實測，風險不值得承擔。
- **替代方案**：`onnxsim` 簡化圖形（多一個依賴且可能改變數值，TensorRT 的解析器自己會做常數折疊，沒有明確收益）。

### 7. TensorRT：Python API 建置與執行

建置（`inference/build_engine.py`）：讀 ONNX 與 sidecar、驗證 ONNX sha256 → `trt.Builder`、顯式批次網路、`OnnxParser` → config 設 `BuilderFlag.FP16`、workspace 8 GiB、預設優化等級、timing cache（從 `<engine>.timing` 讀取，建完寫回）→ `build_serialized_network` → 寫到暫存檔再原子改名 → 寫 sidecar。建置失敗不留下 engine 檔。**不**設定 strict types，讓個別層在 FP16 不適合時由 TensorRT 自行退回 FP32。

執行（`inference/tensorrt_backend.py`）：反序列化 engine、建立 execution context、預先配置輸出張量；`infer` 以 `set_tensor_address` 指向 torch 張量的 `data_ptr()` 後呼叫 `execute_async_v3`。使用專屬的 `torch.cuda.Stream`：先讓它等待目前串流（輸入張量在目前串流上產生），執行後再讓目前串流等待它，使輸出在後續的 `finish` 中可安全使用。這樣避免在預設串流上呼叫而被 TensorRT 額外同步（實測有此警告）。

- **為何不用 pycuda**：實測已證明 torch 張量的指標就能直接執行，少一個依賴與一套裝置記憶體管理。
- 輸出張量固定形狀 `1×23625×85`，與 PyTorch 後端的 `infer`（取 `[0]`）對齊後回傳 `23625×85` 的視圖。

### 8. ONNX Runtime：只用 CUDA 執行提供者，IO binding

`inference/onnx_backend.py`：`InferenceSession(path, providers=["CUDAExecutionProvider"])`，建立後**明確檢查** `session.get_providers()[0] == "CUDAExecutionProvider"`，否則丟出 `BackendError` 並說明（常見原因是 onnxruntime-gpu 的 CUDA 版本與環境不符）。必須如此，因為實測顯示 onnxruntime 在載入失敗時只記錄警告並悄悄退回 CPU。

`infer` 以 `io_binding` 把輸入綁到 torch 張量的 `data_ptr()`、輸出綁到預先配置的 torch 張量，呼叫 `run_with_iobinding` 後以 `synchronize_outputs` 確保完成。匯入順序：後端模組先 `import torch` 再 `import onnxruntime`，使 torch 隨附的 CUDA 與 cuDNN 函式庫先被載入（實測環境即是此順序）。

- TF32：onnxruntime CUDA 執行提供者與 PyTorch 的卷積預設都可能以 TF32 計算。驗收（30 張真實影格、框差 ≤0.5 px、分數差 ≤1e-3）若因此不過，先以執行提供者的 `use_tf32` 選項與 `torch.backends.cudnn.allow_tf32` 對齊兩邊再比；仍不過就如實記錄實際差距並與使用者討論容差，**不**悄悄放寬。這是已知的待驗證點，列在 Open Questions。

### 9. 依賴與版本固定

`pyproject.toml` 新增 `export = ["onnx==1.17.0", "onnxruntime-gpu==1.22.0", "tensorrt-cu12==10.13.3.9"]`。

- `onnxruntime-gpu` 不用範圍版本：1.32.0 已實測在 CUDA 12.8 環境壞掉，`>=` 會被解析到它。
- 升級任一者都要重測 engine 建置與 parity，寫在 `docs/evaluation.md` 的「限制」。
- 使用重依賴的測試用 `pytest.importorskip` 取得模組並標 `gpu`；`inference/artifacts.py` 與 `inference/backends.py` 的測試不需要它們，所以預設 CI 照常覆蓋這兩個檔案。選用後端但缺套件時，後端模組匯入失敗被轉成 `BackendError`，訊息指名缺少的套件並提示 `pip install "openmot-serve[export]"`。

### 10. 載入介面與旗標

- `load_default_detect_frame(weights, precision, device, backend="torch", engine=None)`；`DetectorLoader` 型別同樣在最後加兩個有預設值的參數。worker、api 與既有呼叫端不傳新參數即維持原行為。
- `tracking.run`、`eval.run`、`eval.latency` 新增 `--backend {torch,onnx,tensorrt}`（預設 `torch`）與 `--engine PATH`（預設依命名規則推導）。`eval.run` 與 `tracking.run` 現有測試的假載入函式需補上兩個有預設值的參數；這是測試檔的機械性修改。
- `eval.latency` 的 `Dependencies.load_staged` 改為接受同樣的新參數，預設實作改呼叫 `inference.backends.load_staged_detector`。

### 11. 文件與驗收

- `docs/evaluation.md` 新增「ONNX 與 TensorRT」章節：匯出與建置指令、命名規則、版本固定的理由、各後端的 HOTA 與延遲對照表（附 run name 與環境）、30 張影格的 parity 結果與已知限制。
- 驗收：ONNX（fp32）與 TensorRT（fp16）各跑一次完整 `eval.run`，合併 HOTA 與 PyTorch FP16（50.78）相差不超過 0.5；PyTorch FP32 也跑一次作為對照（快取鍵已含精度，不會混用）。延遲：三個後端各跑 `eval.latency`（50 + 500，兩次）。
- 規則：數字就是量到的數字。如果 HOTA 差距超過 0.5 或 ONNX parity 不過，不調整門檻或悄悄修補，記錄並與使用者討論。

## Risks / Trade-offs

- [TensorRT、onnxruntime 與 CUDA 12.8 加 Blackwell 的組合很緊] → 版本固定；規劃階段已實測；升級需重測並寫入文件。
- [onnxruntime 悄悄退回 CPU，使「ONNX 延遲」其實是 CPU] → 載入後明確檢查 `get_providers()[0]`，並有對應測試（以假 session 模擬）。
- [engine 在錯誤的 GPU 或 TensorRT 版本上被使用] → 檔名加 sidecar 雙重防護，載入時逐項比對。
- [TF32 使 ONNX 與 PyTorch 的 FP32 差距超過容差] → 見決策 8；先對齊 TF32 設定再比，仍不過則如實回報。
- [TensorRT FP16 的偵測與 PyTorch FP16 在框座標上不會逐位相同] → 驗收用 HOTA 差 ≤0.5，而非逐框比對。
- [GPU 測試不在 GitHub CI，回歸可能沒被發現] → 文件寫明這點；`inference/artifacts.py`、`inference/backends.py` 與設定驗證的測試不需要 GPU，預設 CI 會跑；GPU 測試以 `pytest -m "gpu and data"` 在本機執行。
- [輔助檔（timing cache）過期] → timing cache 只加速建置，不影響正確性；TensorRT 會自行判斷是否可用。
- [`[export]` extra 約 1 GB] → 只有需要匯出或跑這兩個後端的人安裝；預設環境與映像不變。

## Migration Plan

無資料遷移，行為預設不變：不帶 `--backend` 時所有入口都使用 `torch`，快取與摘要沿用既有格式。新增 `models/engines/` 的忽略規則後，既有檔案不受影響。回滾即還原此 change 的提交；已建好的產物是本機衍生檔，可直接刪除。

## Open Questions

- ONNX（FP32）與 PyTorch（FP32）在 30 張真實影格上能否落在框差 ≤0.5 px、分數差 ≤1e-3？取決於 TF32 與兩邊的卷積演算法選擇，只能在實作時實測；不過就依決策 8 處理並回報。
- TensorRT FP16 的合併 HOTA 與 50.78 的差距是否在 0.5 內？同樣只能實測。
