# Proposal

## Why

路線圖 v0.5 剩下「INT8 量化（含校準）」與「前後對照」。TensorRT FP16 已經把純推論從 11.2 ms 降到 4.8 ms，但端到端 10.5 ms 裡還有約 6 ms 的前後處理，INT8 只能縮短那 4.8 ms；在動手之前就要說清楚，這個變更要回答的是「INT8 能再省多少、準確度付出多少」，而不是預設它值得。

## What Changes

- `python -m inference.build_engine` 新增 `--precision int8`：以 TensorRT 訓練後量化（entropy calibrator，INT8 搭配 FP16 後備）從既有的 ONNX 檔建出 INT8 engine；預設仍是 FP16，行為不變。
- 校準影格只取自 7 個 FRCNN 序列的 Tuning Half，依固定規則均勻抽樣，前處理與推論時相同；Validation Half 不參與校準。校準的來源、張數、挑選清單雜湊與校準快取雜湊寫進 engine 的 sidecar。
- 後端與精度的對照表改為 `tensorrt` 支援 `fp16` 與 `int8`；`onnx` 與 `torch` 不變。`int8` 成為合法的精度名稱，命令列、評測、延遲量測與 worker 設定都用同一張表驗證。
- 以既有的 Benchmark Run 與延遲量測，在這台機器上量出 FP16 與 INT8 的 HOTA、MOTA、IDF1、延遲與吞吐，寫成前後對照。準確度的標示規則：INT8 的合併 HOTA 與 PyTorch FP16 相差在 1.0 以內才標為「建議使用」，超過就如實標為「不建議」，變更仍算完成。
- 更新 README 的結果表與路線圖，以及 `docs/evaluation.md`。

## Capabilities

### New Capabilities

（無；INT8 是既有後端能力的延伸。）

### Modified Capabilities

- `inference-backends`: engine 的建置接受 INT8；後端與精度的對照加入 `tensorrt` 搭配 `int8`；新增校準資料的規則與 INT8 準確度對照與標示的規則。
- `benchmark-evaluation`: Benchmark Run 可選 `tensorrt` 搭配 `int8`，分數摘要另記錄校準的識別資訊。
- `inference-benchmark`: 不支援的組合的錯誤訊息改為列出 `tensorrt` 支援的兩種精度。

## Impact

- 程式：`tracking/config.py`（精度與對照表）、`inference/build_engine.py`、新增 `inference/calibration.py`、`inference/backends.py` 的 `describe_artifact`、`eval/run.py` 與 `eval/latency.py` 的摘要。
- 不新增套件：校準器與 INT8 旗標都在已固定版本的 `tensorrt-cu12` 內（10.13.3.9 已確認有 `IInt8EntropyCalibrator2` 與 `BuilderFlag.INT8`）。
- 不改：worker 的程式、Dockerfile、compose 與 k8s。`OPENMOT_PRECISION=int8` 因共用同一張對照表而被設定接受，但 compose override 仍預設 `fp16`，服務以 INT8 端對端執行不在這次驗證範圍。
- 不量測 GPU 記憶體：README 的「GPU memory」欄維持 TBD。
- 風險：INT8 可能在這顆 YOLOX-X 上掉分過多，或在 RTX 5090 上沒有明顯加速；兩種結果都要記錄，不調校準直到好看。
