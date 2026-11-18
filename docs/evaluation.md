# 評測流程（Benchmark）

`python -m eval.run` 用一個指令完成整個評測：在每個序列的前半段調參，再於後半段計算 HOTA、MOTA、IDF1，並寫出可貼進 README 的表格。

## 前置條件

- 已建立 conda 環境 `openmot` 並安裝專案（`pip install -e ".[dev]"`），權重檔位於 `models/yolox_x.pth`。
- 已下載 MOT17 訓練序列（見下節）。
- 有可用的 CUDA GPU。指令一開始會做環境檢查，不通過就不會繼續。

可以單獨確認環境：

```bash
python -m tracking.env_check
```

下載資料（需要約數 GB 空間，已下載的目錄會被略過）：

```bash
bash data/download_mot.sh
```

## 切分規則

每個序列依幀數 N 切成兩半，前半是 Tuning Half，後半是 Validation Half：

- Tuning Half：第 1 到 `floor(N/2)` 幀。
- Validation Half：第 `floor(N/2)+1` 到第 N 幀。

例如 600 幀切成 1–300 與 301–600；525 幀切成 1–262 與 263–525。

兩個 Half 都會被實體化成一般的序列資料夾：影格以符號連結（symlink）重新編號為 `000001.jpg…`，`gt/gt.txt` 裁切並重新編號，`seqinfo.ini` 的 `seqLength` 改成該半段的長度。原始 MOT17 目錄不會被修改。

評測使用 7 個 MOT17 訓練序列的 FRCNN 版本（02、04、05、09、10、11、13）。三種偵測器版本的影格與標註相同，而本專案使用自己的偵測結果，不使用 MOT17 附帶的 `det.txt`。

## 調參流程

1. 只實體化 Tuning Half，偵測並把結果快取成 `.npz`。
2. 在快取的偵測上掃描 24 組追蹤參數，以 Tuning Half 的合併 HOTA 選出最佳組合，寫入 `params.json`。
3. 參數確定後才實體化 Validation Half，偵測、用確定的參數追蹤、計分。

Validation Half 在步驟 2 結束前不存在於磁碟上，所以調參不可能讀到它。Validation Half 只用一次，不會回頭調參。

參數網格：

| 參數 | 候選值 |
|---|---|
| 高信心門檻 `detection_threshold` | 0.5、0.6、0.7 |
| 匹配門檻 `match_threshold` | 0.8、0.9 |
| Track 保留幀數 `track_buffer` | 15、30、45、60 |

合併 HOTA 相同時，選網格順序中的第一組。

## 執行

完整評測（7 個序列，`--precision` 預設為 `fp16`）：

```bash
python -m eval.run --precision fp16
```

只想快速確認流程時，可以指定序列子集合與資料夾名稱：

```bash
python -m eval.run --sequences MOT17-05-FRCNN MOT17-09-FRCNN --run-name quick-check
```

成功時結束碼為 0，並印出 Markdown 表格，例如：

```text
| 序列 | HOTA | MOTA | IDF1 |
| --- | ---: | ---: | ---: |
| MOT17-05-FRCNN | 44.29 | 54.36 | 58.39 |
| MOT17-09-FRCNN | 53.56 | 66.01 | 67.54 |
| 合併 | 48.79 | 59.74 | 62.68 |
```

上表只是兩個序列的範例，不是正式結果。

| 參數 | 說明 | 預設 |
|---|---|---|
| `--precision` | 推論精度，`fp16` 或 `fp32` | `fp16` |
| `--weights` | 權重檔路徑 | `models/yolox_x.pth` |
| `--device` | torch 裝置 | 有 CUDA 時為 `cuda` |
| `--data-root` | MOT17 根目錄 | `data/MOT17` |
| `--results-root` | 輸出根目錄 | `data/results` |
| `--run-name` | 本次輸出的資料夾名稱 | UTC 時間戳 |
| `--sequences` | 要評測的序列名稱 | 7 個 FRCNN 訓練序列 |

## 輸出

每次執行寫到 `data/results/<run-name>/`，該目錄已被 git 忽略：

| 檔案 | 內容 |
|---|---|
| `params.json` | 調參選出的追蹤參數 |
| `results/<序列>.txt` | Validation Half 的 MOTChallenge 結果檔 |
| `summary.json` | 各序列與合併的 HOTA、MOTA、IDF1，以及本次的設定 |
| `summary.md` | 可貼進 README 的表格 |
| `halves/`、`tuning/`、`score/` | 實體化的 Half 與計分的中間檔 |

`summary.json` 記錄權重 ID 與 SHA-256、推論後端（`backend`，目前只有 `torch`）、推論精度、偵測器設定、最終追蹤參數、Python／numpy／torch／CUDA／驅動版本與 GPU 型號，讓不同次的結果可以公平比較。

同名的輸出資料夾已存在時，指令會拒絕執行，不會覆蓋也不會刪除；換一個 `--run-name` 即可。

## 快取與可重現性

偵測是最貴的步驟，因此結果快取在 `data/results/cache/`，各次執行共用。快取鍵包含權重 SHA-256、輸入尺寸、推論精度、NMS 設定與推論後端，任何一項改變都會重新偵測，舊的快取不受影響。不同後端的偵測結果不會互相沿用。

相同的偵測輸入與相同的參數會產生逐位元組相同的結果檔。快取命中時偵測器甚至不會被載入。

## 缺少資料時

MOT17 尚未下載時，指令以非 0 的結束碼結束，並提示使用 `data/download_mot.sh`：

```bash
python -m eval.run --data-root /nonexistent/MOT17 --results-root /tmp/openmot_eval_missing
```

## 解讀結果時的注意事項

- 偵測器是 COCO 預訓練權重，沒有針對 MOT17 微調，分數會低於 ByteTrack 論文，請不要與論文數字並列比較。
- Validation Half 從冷啟動開始追蹤，沒有前半段的 Track 狀態。這對所有後續比較一視同仁。
- 可重現性的保證範圍是「相同偵測輸入 → 相同追蹤輸出」。不同 GPU 或驅動上的 FP16 偵測可能有微小差異，因此摘要才記錄這些環境資訊。

## 延遲與吞吐

`python -m eval.latency` 量測偵測器處理單張影格的延遲與吞吐。它回答「這個推論後端有多快」，不回答「準不準」；準確度仍由 `eval.run` 的 HOTA 判斷。

### 量測定義

- **影格來源**：各序列 Validation Half 的影格，依序串接；數量不足時從頭循環。所有影格在計時開始前就讀入記憶體，所以磁碟讀取與影像解碼不計入。預設 7 個序列共約 2,600 張，預設的 550 張約佔 3.4 GB 記憶體。
- **預熱與量測**：先處理 50 張預熱影格（不計入任何數字），再量測 500 張。兩者以 `--warmup`、`--measured` 調整，必須是正整數。
- **兩層延遲**：
  - 純推論：只含推論引擎的一次前向。
  - 偵測端到端：前處理（letterbox、搬到 GPU）、推論、後處理（解碼與 NMS、座標縮放）。追蹤器不計入。
- **同步**：每一段計時前後都等 GPU 完成工作，所以數字是實際完成時間，不是工作排入佇列的時間。
- **兩輪**：端到端與純推論各量一輪，不在同一輪內分段同步，因為階段之間的同步會打斷 GPU 管線，讓端到端數字偏高。
- **統計**：中位數與 p95 以 `numpy.percentile`（線性內插）計算，單位毫秒。吞吐等於量測張數除以各張端到端延遲的總和，不含影格之間的空檔；單串流、batch 1。
- **GPU 記憶體**：以 `nvidia-smi` 讀整張 GPU 的已用量（MiB），在載入偵測器之前、載入之後、量測結束之後各讀一次，結果記錄兩個增量；第一次讀數在程序使用 CUDA 之前取得，所以增量含該程序自己的 CUDA context。讀不到時（例如 `--device cpu`）量測照常完成，`gpu_memory` 標為不可用。計時區間內不讀取。細節與限制見「INT8 量化」一節的「GPU 記憶體」。

### 執行

```bash
python -m eval.latency --precision fp16 --run-name baseline-torch-fp16
```

結果寫到 `data/results/latency/<run-name>/`（已被 git 忽略）：`latency.json` 記錄後端、精度、偵測輸入尺寸、權重 ID 與 SHA-256、預熱與量測張數、序列、軟體版本、GPU 與驅動，以及各項數字；`latency.md` 是可直接閱讀的表格。同名資料夾已存在時指令拒絕執行。MOT17 缺檔、環境檢查失敗或張數無效時，以非 0 的結束碼結束且不寫檔。

### 基準結果（PyTorch FP16）

RTX 5090、驅動 580.126.20、torch 2.7.1+cu128、輸入 800×1440、預設 50 + 500 張，量測前 GPU 使用率 0%、沒有其他運算程序。連續量測兩次：

| 次 | 純推論中位數 / p95 (ms) | 端到端中位數 / p95 (ms) | 吞吐 (FPS) |
| --- | ---: | ---: | ---: |
| 1 | 11.25 / 11.94 | 18.21 / 21.26 | 54.6 |
| 2 | 11.24 / 11.47 | 18.33 / 20.70 | 54.7 |

兩次的中位數相差不到 1%，p95 相差約 3% 到 4%。端到端比純推論多出約 7 ms，這是前處理與後處理的合計耗時，兩者沒有再細分。

### 驗證紀錄

- **階段拆分不改變輸出**：在 RTX 5090、FP16、真實權重上，對 MOT17-09-FRCNN 的前 30 張影格，拆分前（`git` 上一個提交的 `detect`）與拆分後的偵測結果逐元素比對：30 張全部完全相同（共 430 個偵測框，最大差異 0.0）。
- **新的快取鍵不影響分數**：快取鍵加入後端後，既有快取失效，重跑完整 Benchmark Run（`full-fp16-3`）；其 `summary.md` 與改動前的 `full-fp16-2` 逐位元組相同（合併 HOTA 50.78、MOTA 46.14、IDF1 59.82），且 `summary.json` 含 `backend: torch`。
- 改動前的 `summary.json` 沒有 `backend` 欄位，閱讀時視為 `torch`。

### 驗證紀錄（後端抽出前後）

- **階段抽出不改變輸出**：為了讓 ONNX 與 TensorRT 後端重用前處理與後處理，`tracking/detector.py` 新增 `prepare_stage`、`finish_stage`、`synchronize_stage` 與 `run_staged`，`torch_staged_detector` 與 `detect` 改用它們。在 RTX 5090、FP16、真實權重上，對 MOT17-09-FRCNN 的前 30 張影格，抽出前（`main` 的 `tracking/detector.py`，無 `run_staged`）與抽出後的偵測結果逐元素比對：每張的偵測數量相同，共 430 個偵測框，座標與分數完全相同。
- **ONNX 匯出**：`python -m inference.export_onnx` 在 `models/engines/` 寫出 `yolox_x-5652330b-800x1440.onnx`（378.1 MiB）與 `yolox_x-5652330b-800x1440.onnx.json`（323 B）。sidecar 記錄 `opset` 17、`exporter` 為 `torchscript`、`torch_version` 為 `2.7.1+cu128`、`precision` 為 `fp32`，以及權重與 ONNX 的 sha256。`models/engines/` 已被 `.gitignore` 忽略，執行後 `git status` 沒有新的未追蹤檔案。
- **ONNX（FP32）對 PyTorch（FP32）的 parity**：對 MOT17-09-FRCNN 前 30 張影格，以最近框配對比較。兩邊都用預設的 TF32 時，有 1 張影格的偵測數量不同（16 對 15，多出的框分數為 0.10007，貼在信心下限 0.1 上），最大框差 1.84 px、最大分數差 1.4e-3，不符門檻。兩邊都關閉 TF32 時，30 張的偵測數量全部相同，最大框差 0.3775 px（門檻 0.5）、最大分數差 2e-6（門檻 1e-3）。因此 ONNX 後端固定以 `use_tf32=0` 建立 session，使 `fp32` 是嚴格的 FP32；parity 測試也把 PyTorch 參考的 TF32 關閉。
- **TensorRT FP16 engine 建置**：`python -m inference.build_engine` 在 RTX 5090、TensorRT 10.13.3.9、workspace 8 GiB、預設優化等級下寫出 `yolox_x-5652330b-800x1440-trt10.13.3.9-nvidia-geforce-rtx-5090-fp16.engine`（196.3 MiB）、同名的 `.json` sidecar（529 B）與 `.timing` timing cache（1.4 MiB）。第一次建置 242 秒；緊接著的第二次建置讀取並寫回 timing cache（修改時間更新、大小不變、內容雜湊改變），只花 14 秒。sidecar 記錄權重與 ONNX 的 sha256、輸入尺寸、精度、workspace、優化等級（`default`）、旗標（`FP16`）、TensorRT 版本、GPU 型號與 timing cache 檔名。
- **TensorRT（FP16）在真實影格上的偵測數量**：對 MOT17-09-FRCNN 前 30 張影格，TensorRT 回傳與 PyTorch 後端相同型別與欄位的偵測（`float64`、5 欄、分數介於 0.1 與 1.0）。偵測總數為 428，PyTorch FP16 為 430；只有 2 張影格的數量不同（第 27 張 16 對 15、第 28 張 14 對 13）。這一項不設逐框容差，完整的 HOTA 比較見下方。

### 限制

- 數字只代表這一台機器（一張 RTX 5090）與這一組驅動，不可外推到其他硬體。
- GPU 若被其他程序佔用，延遲會變高且不穩；請在閒置的 GPU 上量測，並以多次量測確認波動。
- 量測的是偵測器，不是整條服務：追蹤器、API、佇列與磁碟 I/O 都不在內。
- 只有 batch 1、單串流；多串流或批次推論的吞吐不在此量測範圍。
- 預設 CI 沒有 GPU，這個指令的 GPU 測試（`pytest -m "gpu and data"`）只在本機執行，CI 不覆蓋。

## ONNX 與 TensorRT

偵測網路本體（不含 letterbox、人物篩選與 NMS）可以匯出成 ONNX，再建成 TensorRT FP16 engine。三個後端共用同一套前處理與後處理，只有推論引擎不同，所以延遲與 HOTA 的差異只能歸因於推論引擎。

### 安裝、匯出與建置

重依賴放在獨立的 `[export]` extra，預設安裝、預設測試與預設 CI 都不需要它們：

```bash
pip install -e ".[export]"        # onnx==1.17.0、onnxruntime-gpu==1.22.0、tensorrt-cu12==10.13.3.9
python -m inference.export_onnx   # 權重 -> models/engines/*.onnx，固定 1×3×800×1440、FP32
python -m inference.build_engine  # ONNX -> models/engines/*.engine，FP16
```

使用時在三個入口（`tracking.run`、`eval.run`、`eval.latency`）加上後端與精度，兩者必須明確搭配，不做隱性推斷：

```bash
python -m eval.run --backend onnx --precision fp32
python -m eval.run --backend tensorrt --precision fp16
python -m eval.latency --backend tensorrt --precision fp16 --engine path/to/other.engine
```

支援的組合只有 `torch`＋`fp16`／`fp32`、`onnx`＋`fp32`、`tensorrt`＋`fp16`／`int8`；其他組合在載入模型之前就以非 0 的結束碼拒絕，錯誤訊息列出該後端支援的精度。

### 產物命名與驗證

產物預設放在被 git 忽略的 `models/engines/`，檔名由來源決定，不同權重、GPU 或 TensorRT 版本不會互相覆蓋：

| 產物 | 檔名 |
| --- | --- |
| ONNX | `yolox_x-<權重 sha256 前 8 碼>-800x1440.onnx` |
| engine | `yolox_x-<權重 sha8>-800x1440-trt<TensorRT 版本>-<GPU>-<精度>.engine`（精度為 `fp16` 或 `int8`） |
| sidecar | `<產物檔名>.json`，記錄來源與建置參數 |
| timing cache | `<engine 檔名>.timing`，只加速重建，不影響正確性 |

載入前會逐項比對 sidecar：目前權重檔重新計算的 sha256、ONNX 檔重新計算的 sha256（不只信任 sidecar）、輸入尺寸；engine 另比對 TensorRT 版本與 GPU 型號。任何一項不符就失敗並指名項目；產物不存在時，錯誤訊息含預期檔名與建置指令。`eval.run` 與 `eval.latency` 在建立結果資料夾之前就完成這項驗證，即使偵測快取全部命中、根本不載入模型，過期的產物也會被發現。`summary.json` 與 `latency.json` 另記錄 `artifact`（檔名、ONNX sha256、函式庫版本；TensorRT 另含建置參數與 GPU 型號），PyTorch 後端不多出這個欄位。

### 版本固定的理由

- `onnxruntime-gpu` 固定 1.22.0：1.32.0 需要 CUDA 13，在 CUDA 12.8 的環境載入執行提供者失敗時**不報錯、悄悄退回 CPU**，使「ONNX 延遲」其實是 CPU 的延遲。後端因此在建立 session 後明確檢查第一個執行提供者必須是 `CUDAExecutionProvider`，否則載入失敗，不退回 CPU。
- `tensorrt-cu12` 固定 10.13.3.9：在 RTX 5090（Blackwell）與 CUDA 12.8 上實測可建置 FP16 engine。
- 升級其中任何一個，都要重新建置 engine 並重跑下方的 parity 與 HOTA 比較。

### ONNX 的 FP32 是嚴格的 FP32

onnxruntime 的 CUDA 執行提供者與 PyTorch 的卷積預設都以 TF32 計算，兩邊的捨入不同。在 30 張真實影格上，兩邊都用預設的 TF32 時，ONNX 與 PyTorch 的偵測在門檻邊緣會出現差異（見上方「驗證紀錄」），只有兩邊都關閉 TF32 才符合門檻。因此 ONNX 後端建立 session 時固定傳入 `use_tf32=0`，使 `fp32` 是嚴格的 FP32；代價是 ONNX 的延遲比允許 TF32 時高。PyTorch 的 `fp32` 後端沒有改動，仍使用 PyTorch 的預設設定。

### 準確度對照（HOTA）

Validation Half、7 個序列、各自調參後的合併分數；每次都是冷快取的完整 Benchmark Run（快取鍵含後端與精度，不會混用）：

| Run | 後端 | 精度 | HOTA | MOTA | IDF1 | 與基準 HOTA 的差 |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| `full-fp16-3`（基準） | torch | fp16 | 50.78 | 46.14 | 59.82 | 0 |
| `full-fp32-1` | torch | fp32 | 50.80 | 46.15 | 59.81 | +0.02 |
| `full-onnx-fp32-1` | onnx | fp32 | 50.80 | 46.15 | 59.81 | +0.02 |
| `full-tensorrt-fp16-1` | tensorrt | fp16 | 50.85 | 46.14 | 59.93 | +0.07 |

ONNX 與 TensorRT 的合併 HOTA 都在基準的 0.5 以內；ONNX（fp32）與 PyTorch（fp32）的三項分數相同到小數點後兩位。這個差距只代表這個資料切分與這一次調參的結果，不是 TensorRT 一定比較準。

### 延遲與吞吐對照

RTX 5090、驅動 580.126.20、torch 2.7.1+cu128、onnxruntime-gpu 1.22.0、TensorRT 10.13.3.9、輸入 800×1440、預設 50 + 500 張、batch 1、單串流，量測前 GPU 閒置。每個後端連續量測兩次：

| Run | 後端 | 精度 | 純推論中位數 / p95 (ms) | 端到端中位數 / p95 (ms) | 吞吐 (FPS) |
| --- | --- | --- | ---: | ---: | ---: |
| `lat-torch-fp16-1` | torch | fp16 | 11.24 / 11.64 | 18.11 / 20.26 | 55.3 |
| `lat-torch-fp16-2` | torch | fp16 | 11.25 / 11.59 | 18.14 / 20.44 | 55.2 |
| `lat-onnx-fp32-1` | onnx | fp32 | 36.04 / 36.32 | 45.54 / 48.68 | 21.8 |
| `lat-onnx-fp32-2` | onnx | fp32 | 36.07 / 36.36 | 45.71 / 49.21 | 21.7 |
| `lat-tensorrt-fp16-1` | tensorrt | fp16 | 4.75 / 4.82 | 10.47 / 13.62 | 91.6 |
| `lat-tensorrt-fp16-2` | tensorrt | fp16 | 4.75 / 4.81 | 10.63 / 13.91 | 90.4 |

- 同一後端兩次的純推論中位數相差不到 0.1 ms，端到端中位數相差不到 2%。
- TensorRT（fp16）的純推論約為 PyTorch（fp16）的 2.4 倍快，端到端約 1.7 倍快，吞吐從約 55 FPS 到約 91 FPS。端到端與純推論的差距（前處理與後處理）約 6 ms，在 TensorRT 下已成為延遲的大宗。
- ONNX（fp32）的數字**不能**拿來和 FP16 的兩列比較：它是嚴格的 FP32（TF32 關閉），精度與其他兩者不同。這份對照沒有量測 PyTorch（fp32）的延遲，所以不能由此判斷 ONNX Runtime 與 PyTorch 在同精度下誰快。

### 已知限制

- engine 綁定建置它的 GPU 型號與 TensorRT 版本，換機器或升級後必須重新建置；載入時會被拒絕並提示重建。
- 輸入固定為 1×3×800×1440，不支援動態形狀或批次推論。
- INT8 的做法、結果與限制見下一節「INT8 量化」。
- 服務（worker）以 `OPENMOT_BACKEND`、`OPENMOT_ENGINE` 選擇後端，細節見 [`docs/service.md`](service.md)；INT8 的服務端對端驗證見下一節的「服務端對端」。
- 數字只代表這一台機器（一張 RTX 5090）與這一組驅動與函式庫版本，不可外推到其他硬體。
- 需要 GPU 與 `[export]` 套件的測試（`pytest -m "gpu and data"`）只在本機執行，GitHub CI 不覆蓋；預設的 `pytest` 在沒有安裝 `[export]` 的環境也全數通過。

## INT8 量化

結論先行：在這個資料切分上，INT8 engine 的合併 HOTA 比 PyTorch FP16 低 1.51，超過事先訂下的 1.0 門檻，所以標示為**不建議**；速度則明顯變快（純推論約 1.7 倍快）。兩個結果都照實記錄，下面的規則與數字都是在看到結果之前就固定的。INT8 engine 也已經經由 TensorRT worker 端對端處理過一個真實的 Job（見「服務端對端」），並量了 GPU 記憶體（見「GPU 記憶體」）。

### 做法

- 訓練後量化（PTQ），使用 TensorRT 的 entropy calibrator（`IInt8EntropyCalibrator2`）。建置旗標是 INT8 搭配 FP16：無法量化的層退回 FP16，而不是 FP32。網路的輸入輸出仍是 FP32，所以前處理與後處理與其他後端完全相同。
- 校準影格 512 張，只取自 7 個 FRCNN 訓練序列的 Tuning Half（第 1 到 `floor(N/2)` 幀），Validation Half 的影格不會被使用。先把所有 Tuning Half 影格依（序列名、幀號）排序成一個清單，再取第 `i * 清單長度 // 512` 個（i 從 0 到 511），不使用亂數，所以相同的資料與參數得到相同的清單。校準看到的張量與推論時一樣（letterbox、0–255 的 FP32）。挑選與抽樣的程式在 `inference/calibration.py`。
- 建置與使用：

```bash
python -m inference.build_engine --precision int8     # 另有 --calibration-frames（預設 512）、--data-root（預設 data/MOT17）
python -m eval.run --backend tensorrt --precision int8
python -m eval.latency --backend tensorrt --precision int8
```

- 產物：engine 檔名以 `-int8.engine` 結尾；sidecar 另有 `calibration` 區塊（校準器名稱、張數、挑選清單的 sha256、校準快取的 sha256、資料來源）；校準快取寫在 `<engine>.calib`。重新建置會先刪掉舊的校準快取、從頭校準，所以 sidecar 記錄的挑選清單一定就是這次用的。缺少 MOT17 序列或可用影格不足時，建置在開始之前就失敗，不留下任何檔案。`summary.json` 與 `latency.json` 的 `artifact.build.calibration` 帶出張數與兩個雜湊。
- 載入時的驗證項目沒有增加（權重、ONNX、輸入尺寸、TensorRT 版本、GPU、精度）；校準資料不影響 engine 能不能在目前環境載入，所以不納入。

### 驗收規則（事先固定）

合併 HOTA 與 PyTorch FP16 相差在 1.0 以內，才標為「建議使用」，超過標為「不建議」，變更仍算完成。校準設定（512 張、等距抽樣、INT8 搭配 FP16）在第一次建置前就固定，看過 Validation Half 的結果之後沒有調整；要調整必須另開變更並說明理由。

### 準確度對照（HOTA）

Validation Half、7 個序列、各自調參後的合併分數；每次都是冷快取的完整 Benchmark Run（快取鍵含後端與精度）。差是與 PyTorch FP16 基準的差：

| Run | 後端 | 精度 | HOTA | MOTA | IDF1 | HOTA 差 | MOTA 差 | IDF1 差 |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `full-fp16-3` | torch | fp16 | 50.78 | 46.14 | 59.82 | 0.00 | 0.00 | 0.00 |
| `full-tensorrt-fp16-1` | tensorrt | fp16 | 50.85 | 46.14 | 59.93 | +0.07 | 0.00 | +0.11 |
| `full-tensorrt-int8-1` | tensorrt | int8 | 49.27 | 45.13 | 58.14 | -1.51 | -1.01 | -1.68 |

與同一個執行環境的 TensorRT FP16 相比，INT8 的 HOTA 低 1.58、MOTA 低 1.01、IDF1 低 1.79。

**標示：不建議**（HOTA 差 -1.51，超過 1.0 的門檻）。

### 延遲與吞吐對照

與上一節相同的環境與方法（RTX 5090、輸入 800×1440、50 + 500 張、batch 1、單串流、量測前 GPU 閒置），TensorRT 的 FP16 與 INT8 各連續量測兩次；FP16 兩列是為了這份對照在同一個環境補量的：

| Run | 後端 | 精度 | 純推論中位數 / p95 (ms) | 端到端中位數 / p95 (ms) | 吞吐 (FPS) |
| --- | --- | --- | ---: | ---: | ---: |
| `lat-tensorrt-fp16-cmp-1` | tensorrt | fp16 | 4.75 / 4.81 | 10.38 / 14.09 | 90.8 |
| `lat-tensorrt-fp16-cmp-2` | tensorrt | fp16 | 4.75 / 4.80 | 10.57 / 13.96 | 90.1 |
| `lat-tensorrt-int8-1` | tensorrt | int8 | 2.82 / 2.94 | 7.67 / 11.69 | 122.1 |
| `lat-tensorrt-int8-2` | tensorrt | int8 | 2.82 / 2.88 | 7.40 / 10.80 | 130.5 |

- 純推論：INT8 約 2.82 ms，FP16 約 4.75 ms，約 1.7 倍快；兩次的中位數幾乎相同。
- 端到端：INT8 中位數約 7.4–7.7 ms，FP16 約 10.4–10.6 ms，吞吐約 122–131 FPS 對約 90–91 FPS（約 1.4 倍）。純推論與端到端是兩個不同的倍數，不要互換引用。
- INT8 兩次端到端的吞吐相差約 7%（122.1 與 130.5），比 FP16 兩次（約 0.8%）的波動大；這份對照沒有量測造成這個差別的原因，也沒有更多次的重複，所以 INT8 的吞吐只能當作約略的範圍。
- 與 PyTorch FP16（上一節：純推論 11.2 ms、端到端 18.1 ms、約 55 FPS）相比，TensorRT INT8 的端到端約 2.4 倍快，但準確度的代價見上表。

### 服務端對端

服務端對端已驗證：TensorRT worker 以 INT8 engine 處理了一個真實的 Job。`docker-compose.tensorrt.yml` 的 `OPENMOT_PRECISION` 原本寫死為 `fp16`，現在可由環境變數指定，未設定時仍是 `fp16`：

```bash
OPENMOT_PRECISION=int8 OPENMOT_ENGINE_FILE=<INT8 engine 檔名> docker compose $STACK up -d --build
```

`$STACK` 與其餘步驟見 [`docs/service.md`](service.md) 的「TensorRT worker」與「以 INT8 engine 服務」。驗證用 `tests/test_tensorrt_int8_worker_e2e.py`（RTX 5090、TensorRT 10.13.3.9、MOT17-09 前 60 張影格），結果：

- Job 從提交到 `succeeded` 約 2.3 秒；Result 與中繼檔和同一個 INT8 engine 以命令列（`python -m tracking.run --backend tensorrt --precision int8 --engine …`）處理同一序列的結果逐位元組相同，中繼檔記錄 `"backend": "tensorrt"` 與 `"precision": "int8"`，日誌是 `detector: backend=tensorrt precision=int8 engine=…`。
- 同一個 INT8 engine 以命令列連續執行 3 次，結果逐位元組相同，所以上面的比對才有意義。
- 未指定精度（預設 `fp16`）卻掛載 INT8 engine 時，worker 在處理任何 Job 之前結束，訊息指名精度不符。
- 發現並修正的問題：舊的 `openmot-worker:tensorrt` 映像是在 INT8 支援之前建的，裡面的程式碼只接受 `fp16` 與 `fp32`，worker 與命令列都拒絕 `int8`；重建映像之後才通過。測試與文件都要求先重建。

### GPU 記憶體

量法：用 `nvidia-smi` 讀整張 GPU 的已用量（MiB），在載入偵測器之前、載入之後、量測結束之後各讀一次；第一次讀數在程序使用 CUDA 之前取得。表中「載入後增量」與「量測後增量」都是相對於載入前。GPU 在每次量測前閒置（載入前 4 MiB，沒有其他程序）。每個設定連續量兩次，預設的 50 + 500 張，執行名稱 `lat-mem-<後端>-<精度>-<序號>`：

| Run | 後端 | 精度 | 載入前 (MiB) | 載入後增量 (MiB) | 量測後增量 (MiB) |
| --- | --- | --- | ---: | ---: | ---: |
| `lat-mem-torch-fp16-1` | torch | fp16 | 4 | 922 | 1360 |
| `lat-mem-torch-fp16-2` | torch | fp16 | 4 | 922 | 1360 |
| `lat-mem-tensorrt-fp16-1` | tensorrt | fp16 | 4 | 894 | 1018 |
| `lat-mem-tensorrt-fp16-2` | tensorrt | fp16 | 4 | 894 | 1018 |
| `lat-mem-tensorrt-int8-1` | tensorrt | int8 | 4 | 762 | 886 |
| `lat-mem-tensorrt-int8-2` | tensorrt | int8 | 4 | 762 | 886 |

- 每個設定兩次的數字完全相同，沒有跑間差異。
- 量測結束後：TensorRT FP16 比 PyTorch FP16 少 342 MiB（約 25%），INT8 比 TensorRT FP16 再少 132 MiB（約 13%）。載入後的差距小得多（922、894、762），量測期間 PyTorch 多占了 438 MiB，TensorRT FP16 與 INT8 各多占 124 MiB；這份對照沒有分析造成這個差別的原因。
- 這六次也量了延遲，純推論的中位數與先前記錄的相同（約 11.2、4.75、2.83 ms），但 TensorRT FP16 的端到端中位數是 11.69 與 11.09 ms，高於先前兩次的 10.38 與 10.57 ms（PyTorch FP16 與 INT8 沒有超出先前的範圍）。純推論不變而端到端變高，只有含 CPU 前後處理的部分受影響；事後查到主機的 CPU 負載偏高（24 核心、1 分鐘平均約 13.6），所以懷疑是 CPU 競爭，但量測當下沒有記錄負載，這個解釋沒有被驗證。因此 README 的延遲欄沿用先前記錄的數字，這一批只用於記憶體。

限制：量的是整張 GPU 的已用量，含該程序的 CUDA context 與配置器的快取，不是模型權重的大小；只取三個時間點，載入過程的瞬間峰值取不到；GPU 上有其他程序時數字會偏，所以只在閒置的 GPU 上有意義（載入前的讀數就是用來看這件事）；只是單一程序、單串流的量測，不是服務中 worker 的長期占用。

### 限制

- 只試了一組校準設定（512 張、等距抽樣、entropy calibrator）。沒有研究其他張數、其他校準器或逐層保留 FP16，所以不能說「INT8 一定掉分」，只能說「這一組設定掉了 1.51」；是否有能達標的設定不在這次的範圍。
- 校準資料與評測資料來自同一個資料集的不同半段（Tuning Half 與 Validation Half），不是獨立資料集。
- `IInt8EntropyCalibrator2` 在 TensorRT 10 被標為棄用（隱式量化），目前能用是因為版本固定在 10.13.3.9；升級 TensorRT 時需要重新評估這個做法。
- 同一個 INT8 engine 在 30 張真實影格上連續執行 3 次，偵測結果逐位元組相同；但沒有量測「用相同資料重建兩次 INT8 engine」是否得到相同的結果，所以準確度比較只用同一個 engine，不拿兩次重建互比。
- 服務端對端只驗證了一個 Job（60 張影格）與一個 engine：engine 是 host 上建好再複製進卷的，沒有在映像內重建 INT8 engine，也沒有長時間或多個 Job 的壓力測試。Kubernetes 沒有 TensorRT 映像，仍是 PyTorch，所以 k8s 上的 INT8 沒有驗證。
- GPU 記憶體只取三個時間點的整張 GPU 讀數（見「GPU 記憶體」），不含瞬間峰值，也沒有量服務中 worker 的長期占用。
- 數字只代表這一台機器（一張 RTX 5090）與這一組驅動與函式庫版本，不可外推到其他硬體。
