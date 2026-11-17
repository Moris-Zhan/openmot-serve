# Tasks

## 1. 後端與精度組合

- [x] 1.1 先寫失敗測試：`DetectorSettings` 接受 `torch`+`fp16`、`torch`+`fp32`、`onnx`+`fp32`、`tensorrt`+`fp16`；拒絕 `onnx`+`fp16` 與 `tensorrt`+`fp32` 且訊息列出該後端支援的精度；未知後端被拒絕並列出三個後端；既有的 `torch` 預設行為不變。再於 `tracking/config.py` 新增 `SUPPORTED_PRECISIONS_BY_BACKEND`（`SUPPORTED_BACKENDS` 改為其鍵），並更新 `DetectorSettings.__post_init__`。驗證：`pytest tests/test_detector.py` 通過。
- [x] 1.2 確認 `CacheKey`、`summary.json`、`latency.json` 對新後端名稱照常記錄（不同後端的鍵不同、同後端相同）。驗證：`pytest tests/test_detection_cache.py tests/test_run.py tests/test_latency.py` 通過。

## 2. 階段重用

- [x] 2.1 先寫失敗測試：`run_staged(staged, image)` 與 `detect` 對同一張影像與假模型輸出逐元素相等；`prepare`、`finish`、`synchronize` 的抽出函式只依賴 `DetectorSettings` 與裝置，不需要模型即可建立（以假張量驗證）。再於 `tracking/detector.py` 新增 `run_staged` 並抽出三個階段，讓 `torch_staged_detector` 與 `detect` 改用它們，行為不變。驗證：`pytest tests/test_detector.py tests/test_detector_stages.py` 通過。
- [x] 2.2 在真實權重與 GPU 上，對 30 張影格比對抽出前後（前一提交與目前）的偵測輸出完全相同，結果記入 `docs/evaluation.md` 的驗證紀錄。驗證：比對輸出為相同。

## 3. 產物命名、sidecar 與驗證（不需要 GPU）

- [x] 3.1 先寫失敗測試：ONNX 與 engine 檔名由模型名稱、權重 sha8、輸入尺寸組成，engine 另含 TensorRT 版本、GPU slug 與精度；不同權重、不同 GPU、不同 TensorRT 版本得到不同檔名；GPU slug 對 `NVIDIA GeForce RTX 5090` 得到 `nvidia-geforce-rtx-5090`。再建立 `inference/__init__.py` 與 `inference/artifacts.py` 的命名函式。驗證：`pytest tests/test_artifacts.py` 通過。
- [x] 3.2 先寫失敗測試：sidecar 寫入為鍵排序的 JSON、讀回相等；驗證函式對權重 sha256、ONNX sha256、輸入尺寸、TensorRT 版本、GPU 型號逐項比對，任一不符丟出 `ArtifactError` 並指名該項與兩邊的值；全部相符時通過。再實作讀寫與驗證。驗證：同上測試檔通過。
- [x] 3.3 先寫失敗測試：產物或 sidecar 不存在時，錯誤訊息含預期檔名與對應的建置指令；明確路徑覆寫時使用該路徑並同樣驗證；ONNX sha256 以重新計算的檔案雜湊比對而非只信 sidecar（竄改檔案內容即失敗）。再實作 `resolve_artifact`（不匯入 onnxruntime 與 tensorrt 的執行階段；以注入的假環境觀測值測試）。驗證：同上測試檔通過，且在未安裝 `[export]` 的環境也通過。

## 4. 依賴與專案設定

- [x] 4.1 在 `pyproject.toml` 新增 `export` extra（`onnx==1.17.0`、`onnxruntime-gpu==1.22.0`、`tensorrt-cu12==10.13.3.9`）並把 `inference*` 加進套件清單；在 `.gitignore` 加入 `models/engines/`。驗證：先寫測試斷言三個版本都是固定的 `==`、`inference*` 在套件清單中、`.gitignore` 含 `models/engines/`；`uv pip install -e ".[dev]"`（不含 `export`）後 `pytest` 全數通過。
- [x] 4.2 先寫失敗測試：選用 `tensorrt` 或 `onnx` 後端但套件缺少時，載入失敗並指名缺少的套件與 `[export]` extra 的安裝方式，不以其他後端代替。再在 `inference/backends.py` 把匯入失敗轉成 `BackendError`。驗證：測試以假的匯入失敗通過。

## 5. ONNX 匯出

- [x] 5.1 先寫失敗測試（以微型假模型與假的匯出函式，不載入真實權重）：匯出流程依序驗證權重、匯出到暫存檔、以結構檢查驗證、計算 sha256、寫 sidecar、原子改名；權重雜湊不符時不寫任何檔案；目標檔已存在時拒絕且不改動它。再實作 `inference/export_onnx.py`（`dynamo=False`、opset 17、`1×3×800×1440`、FP32）。驗證：`pytest tests/test_export_onnx.py` 通過。
- [x] 5.2 加 `gpu` 標記的測試（`pytest.importorskip("onnx")`）：在真實權重上匯出，產物通過 `onnx.checker`、輸入形狀為 `1×3×800×1440`、sidecar 與檔案一致。驗證：`pytest -m gpu tests/test_export_onnx_gpu.py` 在本機通過，預設 `pytest` 不收集。
- [x] 5.3 真實執行 `python -m inference.export_onnx`，確認 `models/engines/` 出現預期檔名的 ONNX 與 sidecar，且 `git status` 沒有新的未追蹤檔案。驗證：手動檢查並把檔名與大小記入 `docs/evaluation.md`。

## 6. ONNX 後端

- [x] 6.1 先寫失敗測試（假 session）：請求 CUDA 執行提供者但 `get_providers()` 第一項不是 `CUDAExecutionProvider` 時載入失敗並說明原因，不退回 CPU；建立 session 時只傳 `CUDAExecutionProvider`。再實作 `inference/onnx_backend.py` 的載入與檢查。驗證：`pytest tests/test_onnx_backend.py` 通過。
- [x] 6.2 實作 `infer`：以 IO binding 把輸入綁到 torch 張量、輸出綁到預先配置的 torch 張量，回傳 `23625×85` 的 GPU 張量；`inference/backends.py` 的 `load_staged_detector` 對 `onnx` 組出 `StagedDetector`（重用 PyTorch 的 `prepare`、`finish`、`synchronize`）。以假 session 測試輸入與輸出的繫結、回傳形狀與型別、推論階段沒有主機拷貝。驗證：`pytest tests/test_onnx_backend.py tests/test_backends.py` 通過。
- [x] 6.3 加 `gpu` 與 `data` 標記的 parity 測試：30 張 MOT17 真實影格，ONNX（fp32）對 PyTorch（fp32）的偵測數量相同、框座標差 ≤0.5 px、分數差 ≤1e-3。若不過，依 design 決策 8 先對齊 TF32 設定再比，仍不過就記錄實際差距並暫停回報，不放寬容差。驗證：`pytest -m "gpu and data" tests/test_backend_parity_gpu.py -k onnx` 通過，或已記錄差距並取得使用者決定。

## 7. TensorRT 建置與後端

- [x] 7.1 先寫失敗測試（假 builder）：建置流程驗證 ONNX sha256、設定 FP16 旗標、8 GiB workspace、預設優化等級與 timing cache，成功時寫 engine 與含全部建置參數的 sidecar；缺 ONNX 時指向匯出指令；建置失敗（假 builder 回傳 `None`）時不留下 engine 檔。再實作 `inference/build_engine.py`。驗證：`pytest tests/test_build_engine.py` 通過。
- [x] 7.2 先寫失敗測試（假 engine 與假 context）：`infer` 用 torch 張量的 `data_ptr()` 設定輸入與輸出位址、使用專屬串流並與目前串流雙向等待、回傳 `23625×85` 的 GPU 張量；載入前驗證 TensorRT 版本與 GPU 型號。再實作 `inference/tensorrt_backend.py`，並在 `load_staged_detector` 加入 `tensorrt`。驗證：`pytest tests/test_tensorrt_backend.py tests/test_backends.py` 通過。
- [x] 7.3 真實執行 `python -m inference.build_engine`，建出 FP16 engine 與 sidecar；再以第二次建置確認 timing cache 被讀取並寫回。驗證：engine 與 sidecar 存在、sidecar 欄位齊全，建置時間與大小記入 `docs/evaluation.md`。
- [x] 7.4 加 `gpu` 與 `data` 標記的測試：TensorRT 後端在 30 張真實影格上能完成偵測、回傳型別與欄位與 PyTorch 後端相同，且偵測數量與 PyTorch FP16 的差距記錄下來（不設逐框容差）。驗證：`pytest -m "gpu and data" tests/test_backend_parity_gpu.py -k tensorrt` 通過。

## 8. 載入介面與指令旗標

- [x] 8.1 先寫失敗測試：`load_default_detect_frame` 與 `DetectorLoader` 在最後接受 `backend`（預設 `torch`）與 `engine`（預設 `None`）；不傳新參數時行為與先前相同（worker 與 api 不需修改）。再修改 `tracking/run.py`。驗證：`pytest tests/test_run_cli.py tests/test_worker*.py` 通過，且 worker 與 api 的程式碼無變動。
- [x] 8.2 先寫失敗測試：`tracking.run`、`eval.run`、`eval.latency` 接受 `--backend` 與 `--engine`；預設為 `torch`；`--backend onnx` 未同時給 `--precision fp32` 時在載入模型之前報錯並指出正確精度；缺產物或驗證失敗時以非 0 結束碼結束、不寫結果資料夾。再修改三個入口，`eval.run` 與 `eval.latency` 在建立結果資料夾之前呼叫 `resolve_artifact`。同時補齊 `tests/test_run.py` 等假載入函式的新參數。驗證：`pytest tests/test_run.py tests/test_latency_cli.py tests/test_run_cli.py` 通過。
- [x] 8.3 先寫失敗測試：`summary.json` 與 `latency.json` 在使用 `onnx` 或 `tensorrt` 時記錄產物檔名、ONNX sha256 與函式庫版本（TensorRT 另含建置參數與 GPU 型號），且取自驗證通過的 sidecar；`torch` 後端不多出這些欄位。再實作寫入。驗證：同上測試檔通過。

## 9. 真實驗證與文件

- [x] 9.1 在閒置的 RTX 5090 上依序完成：PyTorch FP32 的 `eval.run`（對照）、ONNX（fp32）的 `eval.run`、TensorRT（fp16）的 `eval.run`；以 `summary.md` 與既有 `full-fp16-3` 並排。驗證：ONNX 與 TensorRT 的合併 HOTA 與 50.78 相差不超過 0.5；若超過，記錄實際數字並暫停回報，不調整門檻。
- [x] 9.2 對 `torch`（fp16）、`onnx`（fp32）、`tensorrt`（fp16）各以預設參數（50 + 500）執行 `eval.latency` 兩次，記錄兩次差異。驗證：`data/results/latency/` 下有六份完整結果，每份的 `backend` 與 `precision` 與指令一致。
- [x] 9.3 在 `docs/evaluation.md` 新增「ONNX 與 TensorRT」章節（繁體中文）：匯出與建置指令、命名規則與驗證、版本固定的理由（含 `onnxruntime-gpu` 1.32.0 不可用）、三個後端的 HOTA 與延遲對照表（附 run name 與環境）、30 張影格的 parity 結果、已知限制（engine 綁 GPU 與 TensorRT 版本、GPU 測試不在 GitHub CI、數字只代表這台機器）。驗證：文件內的每個數字都能在對應的結果資料夾找到。
- [x] 9.4 更新 `README.md` 路線圖，勾選 v0.5 的 ONNX 與 TensorRT 項目並維持其餘項目未勾選。驗證：只有該項目被勾選。
- [x] 9.5 最終檢查：預設 `pytest` 全數通過（不含 `gpu`、`data`、`integration`）、`pytest -m "gpu and data"` 在本機通過、`ruff check` 乾淨、`openspec validate add-onnx-tensorrt-export --strict` 通過，且在未安裝 `[export]` 的環境匯入 `tracking`、`eval`、`worker`、`app` 都不失敗。驗證：貼出各指令的結果。
