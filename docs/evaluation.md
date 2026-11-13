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

### 限制

- 數字只代表這一台機器（一張 RTX 5090）與這一組驅動，不可外推到其他硬體。
- GPU 若被其他程序佔用，延遲會變高且不穩；請在閒置的 GPU 上量測，並以多次量測確認波動。
- 量測的是偵測器，不是整條服務：追蹤器、API、佇列與磁碟 I/O 都不在內。
- 只有 batch 1、單串流；多串流或批次推論的吞吐不在此量測範圍。
- 預設 CI 沒有 GPU，這個指令的 GPU 測試（`pytest -m "gpu and data"`）只在本機執行，CI 不覆蓋。
