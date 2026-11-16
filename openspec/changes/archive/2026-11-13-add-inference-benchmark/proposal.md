# Proposal

## Why

路線圖 v0.5 要比較 ONNX、TensorRT 與 INT8 相對於 PyTorch FP16 的延遲、吞吐與 HOTA。HOTA 的基準已經有了（`benchmark-evaluation` 與 `data/results/full-fp16-*`），但延遲與吞吐沒有任何量測：沒有 baseline，後面的優化就只能憑感覺說「變快了」。這個 change 先量出 PyTorch FP16 的 baseline，並讓偵測快取不會在不同推論後端之間混用，為後續兩個 change 鋪路。

## What Changes

- 新增 `python -m eval.latency`：從 MOT17 的 Validation Half 取影格，預熱後以 batch 1 量測，回報兩層延遲（純推論、偵測端到端）的中位數與 p95，以及吞吐（FPS）。追蹤器與影像解碼不計入。
- 量測結果寫入 `data/results/latency/<run-name>/`（`latency.json` 與 `latency.md`），記錄後端、精度、權重識別、GPU、驅動與軟體版本、預熱與量測張數。
- `DetectorSettings` 新增 `backend`（目前只接受 `torch`）；偵測快取的鍵與分數摘要都記錄後端。**BREAKING**（僅限本機衍生資料）：快取鍵改變，既有 `data/results/cache` 的項目會失效並在下次執行時重算一次；舊的 `summary.json` 不受影響。
- 把 `tracking/detector.py` 的 `detect` 拆成前處理、推論、後處理三個階段，並維持輸出完全一致，讓量測能分別計時；後續 change 的新後端只要提供同樣三個階段。
- 在 PyTorch FP16 上實測一次 baseline，數字寫進 `docs/evaluation.md`；更新 README 路線圖。
- 不包含：ONNX 匯出、TensorRT、INT8、後端選擇的環境變數與服務整合（屬後兩個 change）；追蹤器與端到端服務吞吐；多 batch 或多串流量測；重跑 HOTA 基準。

## Capabilities

### New Capabilities
- `inference-benchmark`: 以固定、可重現的方式量測偵測器的延遲與吞吐，並記錄足以公平比較的環境資訊。

### Modified Capabilities
- `benchmark-evaluation`: 分數摘要要記錄推論後端；不同推論後端的偵測快取不得互相沿用。

## Impact

- **新增檔案**：`eval/latency.py`、`docs/evaluation.md` 的新章節，以及對應測試。
- **修改檔案**：`tracking/detector.py`（階段拆分，行為不變）、`eval/detection_cache.py`（快取鍵）、`eval/run.py`（摘要記錄後端）、`README.md`。
- **外部系統**：無新依賴。
- **風險**：量測受 GPU 上其他程序影響（本機 GPU 已有其他容器佔用記憶體）；延遲數字只代表這台 RTX 5090，不可外推。快取鍵改變會讓下一次 Benchmark Run 重新偵測一遍，約多花一次完整偵測的時間。
