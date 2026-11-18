# Tasks

## 1. 精度名稱與驗證（inference-backends、inference-benchmark）

- [x] 1.1 先寫失敗的測試再修改 `tracking/config.py`：`SUPPORTED_PRECISIONS` 加入 `int8`，`SUPPORTED_PRECISIONS_BY_BACKEND["tensorrt"]` 改為 `("fp16", "int8")`；驗證：新測試涵蓋「`tensorrt` 搭配 `fp16` 與 `int8` 被接受」「`torch`、`onnx` 搭配 `int8` 被拒絕且訊息列出該後端支援的精度」「`tensorrt` 搭配 `fp32` 的訊息列出 `fp16` 與 `int8`」，並涵蓋 `DetectorSettings`、`RunMetadata`、`jobs.settings.load_settings`（`OPENMOT_BACKEND=tensorrt` 搭配 `OPENMOT_PRECISION=int8` 被接受、`onnx` 搭配 `int8` 被拒絕並指名兩個變數）；既有的後端、設定與命令列測試若斷言舊的錯誤文字，只改文字不改意圖並在回報中列出；`ruff check .` 乾淨
- [x] 1.2 先寫失敗的測試再確認命令列：`python -m tracking.run`、`eval.run`、`eval.latency` 的 `--precision int8` 與 `--backend tensorrt` 通過、與 `torch`／`onnx` 搭配時在載入模型之前以非 0 結束並說明；驗證：以假載入器的測試確認三個入口的行為，沒有載入模型、沒有寫出結果檔或資料夾；既有測試通過

## 2. 校準資料的挑選與校準器（inference-backends）

- [x] 2.1 先寫失敗的測試再新增 `inference/calibration.py` 的挑選函式：對 7 個 FRCNN 序列以 `eval.split.split_sequence` 取各自的 Tuning Half，依（序列名、幀號）排序後以固定步長均勻取預設 512 張，回傳清單與其 sha256；驗證：以合成的序列資料夾測試「每一張都在其序列的 Tuning Half 內、沒有 Validation Half 的影格」「相同輸入兩次的清單與雜湊相同」「步長與張數的邊界（影格剛好等於或少於要求）」「找不到序列或可用影格少於要求時拋出指名缺少項目的錯誤」，且模組在沒有 torch 與 tensorrt 的環境也能 import 挑選函式
- [x] 2.2 先寫失敗的測試再新增 entropy calibrator 的封裝：以 `prepare_stage` 產生與推論相同的 FP32 張量，逐張提供給 TensorRT 的 `IInt8EntropyCalibrator2`，並讀寫校準快取；驗證：以假的 TensorRT 模組與假張量測試「batch 大小為 1、輸入記憶體位址與名稱正確、挑選清單用完後回傳結束」「快取存在時讀取、不存在時回傳空並在校準後寫出」「快取的 sha256 可計算」；預設測試套件在沒有 `[export]` extra 的環境通過或略過

## 3. INT8 engine 的建置（inference-backends）

- [x] 3.1 先寫失敗的測試再修改 `inference/build_engine.py`：`BuildSettings` 加 `precision` 與校準設定，`--precision int8`、`--calibration-frames`、`--data-root` 命令列選項，`int8` 時設 `INT8` 與 `FP16` 旗標並掛上校準器，sidecar 的 `builder_flags` 記錄實際旗標並新增 `calibration` 區塊（校準器名稱、張數、挑選清單雜湊、快取雜湊、資料來源描述）；驗證：測試涵蓋「預設仍只設 FP16 旗標、檔名與 sidecar 內容與修改前相同（以固定預期值比對）」「`int8` 的旗標、檔名含 `-int8`、sidecar 內容」「缺校準資料時在建置之前失敗、沒有 engine、sidecar 與 `.calib`」「建置失敗或中途例外不留下不完整的 engine」；既有的 `test_build_engine.py` 不修改且全部通過（`build_with_tensorrt` 既有的位置參數呼叫方式保持可用）
- [x] 3.2 先寫失敗的測試再修改 `inference/backends.py` 的 `describe_artifact`：INT8 engine 的 `build` 區塊含校準識別（張數、挑選清單雜湊、快取雜湊），FP16 與 ONNX 的輸出不變；驗證：測試比對 FP16 輸出與修改前相同，並涵蓋 INT8 的欄位；`eval.run` 與 `eval.latency` 的摘要測試確認 INT8 摘要帶校準識別、精度記為 `int8`、偵測快取的鍵含精度（INT8 不會沿用 FP16 的快取）

## 4. 在這台機器上建置與量測（inference-backends、benchmark-evaluation、inference-benchmark）

- [x] 4.1 先寫 `gpu` 與 `data` 標記的測試 `tests/test_int8_engine_gpu.py`（缺 GPU、TensorRT、權重、MOT17 時略過並說明）再實際建置：在 `tensorrt` 映像或已安裝 `[export]` 的環境以 `python -m inference.build_engine --precision int8` 建出 INT8 engine；驗證：engine 與 sidecar 寫出、sidecar 的校準區塊與挑選清單相符、載入通過驗證、對 30 張真實影格能偵測；先量測同一個 INT8 engine 連續執行 3 次的結果是否逐位元組相同並記錄（決定之後比對的措辭）；若建置在 RTX 5090 上失敗或 INT8 層不被支援，停下來回報原因，不悄悄改規格或改用別的量化方式
- [x] 4.2 以固定的校準設定（512 張、旗標 INT8 搭配 FP16，不因結果調整）執行完整的冷快取 Benchmark Run（`--backend tensorrt --precision int8`）與 `eval.latency` 連續兩次，並以同一個環境補量 TensorRT FP16 的延遲兩次作為對照；驗證：結果資料夾與摘要記錄 `int8` 與校準識別，記錄合併 HOTA、MOTA、IDF1、純推論與端到端延遲的中位數與 p95、吞吐，並計算與 PyTorch FP16（50.78）及 TensorRT FP16（50.85）的差；若 HOTA 差距超過 1.0，照實標為「不建議」，不調校準設定重跑

## 5. 文件與對照（inference-backends）

- [x] 5.1 更新 `docs/evaluation.md`、`README.md` 並以測試鎖住：`docs/evaluation.md` 新增 INT8 一節（校準的來源、張數、抽樣規則、INT8 搭配 FP16 後備的意思、建置指令、棄用的校準器與版本固定、`int8` 被 worker 設定接受但服務端對端未驗證、不量測 GPU 記憶體）與 FP16 對 INT8 的準確度與速度對照表，以事先寫好的規則標示「建議使用」或「不建議」；README 的準確度與效能表填入 INT8 列（GPU memory 欄維持 TBD 並說明未量測），路線圖勾選「INT8 quantization with calibration」與「Before/after comparison」；驗證：文件測試確認表中數字與 4.2 的紀錄一致、差距與標示符合 1.0 的規則、文件指令與檔名和 repo 一致、限制都有寫出、README 的狀態測試通過，沒有把 4.2 沒量到的部分說成已驗證

## 6. 整體檢查（integration）

- [x] 6.1 執行預設的完整測試、`ruff check .`、`openspec validate add-int8-quantization --strict` 與 `openspec validate --specs --strict`，並以 `-m "gpu or data"` 執行 4.1 的測試；驗證：全部通過，預設測試沒有因本變更新增被略過的測試（需要 GPU、TensorRT、MOT17 的除外），既有測試除 1.1 列出的文字更新外沒有被修改

## Workflow follow-up

- 完成後用 `/opsx:archive` 封存這個變更並把 delta spec 同步到三份主規格（`inference-backends` 修改兩條並新增兩條需求；`benchmark-evaluation` 與 `inference-benchmark` 各取代一條同名需求）。
- 後續（不在這個變更內）：服務以 INT8 端對端執行的驗證、GPU 記憶體量測、校準張數與方法的研究、MOT20。
