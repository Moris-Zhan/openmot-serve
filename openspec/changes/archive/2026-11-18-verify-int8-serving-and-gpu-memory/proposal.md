# Proposal

## Why

v0.5 的 INT8 變更留下兩個文件上明講的缺口：INT8 engine 還沒有經過 worker 端對端跑過一次真實的 Job（只在命令列與評測裡驗證），README 結果表的「GPU memory」兩格仍是 TBD。另外 `docs/service.md` 的環境變數表還寫著「`tensorrt` 只支援 `fp16`」，與程式已接受 `int8` 不符。這台機器的 GPU 目前閒置，k3s 又無法在這台執行，補這些缺口是現在做得到、而且能讓 v0.5 的結論站得住腳的事。

## What Changes

- **服務端的 INT8 驗證**：TensorRT 的 compose override 目前把 `OPENMOT_PRECISION` 寫死為 `fp16`，所以 worker 根本無法以 INT8 啟動。改為 `${OPENMOT_PRECISION:-fp16}`，未設定時行為與現在逐位元組相同。
- 新增一個 `gpu` 與 `integration` 標記的端對端測試：以獨立的 compose 專案與卷，把已建好的 INT8 engine 放進去，讓 TensorRT worker 以 `int8` 處理一個真實的 Job，並驗證 Result 與同一個 engine 的命令列結果逐位元組相同、worker 日誌與中繼檔記錄 `int8`。缺 engine、Docker 或 GPU 時 skip 並說明如何建置。
- **GPU 記憶體量測**：`python -m eval.latency` 在載入偵測器前後與量測結束後各讀一次整張 GPU 的已用記憶體，結果檔與表格新增記憶體欄位；所有後端與精度用同一種量法，使數字可以比較。
- 在這台機器上量 PyTorch FP16、TensorRT FP16、TensorRT INT8 各兩次，把記憶體填進 README 的結果表（取代兩個 TBD），並補上 PyTorch FP16 一列。
- **修正映像內的 engine 建置**（驗證時發現，納入這個變更）：`inference/calibration.py` 在 import 階段匯入映像裡沒有的 `eval` 套件，使映像內 `python -m inference.build_engine` 連 FP16 都失敗。把匯入延後到真正需要時，並加回歸測試（design D8）。
- 更新 `docs/service.md`（修正過時的精度敘述，補上 INT8 服務的啟動方式與驗證結果）、`docs/evaluation.md` 的 INT8 一節（把「服務端對端未驗證」「GPU 記憶體未量測」改成實測結果與剩下的限制）與 README。

## Capabilities

### New Capabilities

（無；都是既有能力的延伸。）

### Modified Capabilities

- `inference-benchmark`: 延遲量測的結果新增 GPU 記憶體的記錄與量法的定義。
- `container-deployment`: TensorRT worker 可以用 compose override 以 `int8` 服務，並以 INT8 engine 端對端處理 Job。

## Impact

- 程式與設定：`eval/latency.py`（記憶體探針與結果欄位）、`deploy/docker/docker-compose.tensorrt.yml`（一行環境變數）、新增 `tests/test_tensorrt_int8_worker_e2e.py` 與記憶體量測的單元測試。
- 文件：README、`docs/service.md`、`docs/evaluation.md`；既有的 `tests/test_int8_docs.py` 與 `tests/test_latency_docs.py` 中斷言「服務端對端未驗證」「GPU 記憶體未量測」的部分會因為事實改變而必須更新，這是這個變更該改的測試，不是為了讓測試通過而改。
- 不新增套件：記憶體讀 `nvidia-smi` 的輸出（驅動本來就有），不引入 `pynvml`；讀不到時量測照常完成、記憶體標為不可用。
- 不改：worker 與 api 的程式、Dockerfile、k8s 清單、INT8 的建置與校準流程、準確度的結論（INT8 仍是「不建議」）。
- 資源安全：端對端測試只使用 `openmot-trt-e2e*` 的名稱；`openmot-weights` 只以唯讀使用；不動 `openmot` 專案的容器、卷（`openmot-weights`、`openmot_storage`、`openmot_redis-data`）與映像（`openmot-worker:dev`）。
- 風險：
  - 記憶體用整張 GPU 的已用量相減，含 CUDA context 與 PyTorch 的快取配置器，不是模型權重本身的大小；GPU 上有其他程序時數字會偏。量測前記錄基準值，並在文件註明只在閒置的 GPU 上有意義。
  - INT8 engine 是在 host 的 conda 環境建的，映像內的 TensorRT 版本必須相同；不同時 worker 會依載入驗證拒絕啟動，這本身就是要記錄的結果，不去繞過驗證。
  - 在映像內重建 INT8 engine（需要掛載 MOT17 校準影格）不在這次範圍，只驗證「host 建的 engine 能被 worker 服務」。
