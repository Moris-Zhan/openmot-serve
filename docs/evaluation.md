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

`summary.json` 記錄權重 ID 與 SHA-256、推論精度、偵測器設定、最終追蹤參數、Python／numpy／torch／CUDA／驅動版本與 GPU 型號，讓不同次的結果可以公平比較。

同名的輸出資料夾已存在時，指令會拒絕執行，不會覆蓋也不會刪除；換一個 `--run-name` 即可。

## 快取與可重現性

偵測是最貴的步驟，因此結果快取在 `data/results/cache/`，各次執行共用。快取鍵包含權重 SHA-256、輸入尺寸、推論精度與 NMS 設定，任何一項改變都會重新偵測，舊的快取不受影響。

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
