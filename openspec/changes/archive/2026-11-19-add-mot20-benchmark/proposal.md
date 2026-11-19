# Proposal

## Why

README 的路線圖把「MOT20 benchmark」列在 Later，評測協定也寫著「MOT20 在 v0.5 之後評測」。v0.5 已完成，這台 RTX 5090 機器可以實跑。MOT20 是高密度人群（單格上百個行人），和 MOT17 的場景不同，能檢驗目前 COCO 預訓練的 YOLOX 加 ByteTrack 在擁擠場景的表現。

目前的評測工具有兩處寫死 MOT17：預設序列與資料根目錄，以及送給 TrackEval 的 `BENCHMARK: "MOT17"`。MOT20 的計分慣例多一個類別：標為 `non_mot_vehicle` 的標註在 MOT20 也算干擾物，只有 `BENCHMARK` 設為 `MOT20` 時 TrackEval 才會處理它。直接把資料根目錄指到 MOT20 不會報錯，但計分會悄悄偏掉。

## What Changes

- `python -m eval.run` 新增 `--benchmark {MOT17,MOT20}`（預設 `MOT17`，行為與現在逐位元組相同）。它決定預設的資料根目錄、預設序列，以及 TrackEval 的計分慣例；`--data-root` 與 `--sequences` 仍可覆寫。MOT20 的預設序列為有公開標註的四個訓練序列 `MOT20-01`、`02`、`03`、`05`（下載後以實際目錄確認）。
- 計分與調參把 benchmark 傳給 TrackEval，使 MOT20 套用 `non_mot_vehicle` 干擾物規則。
- 新增 `--params <檔案>`：使用既有的 `params.json` 作為追蹤器參數，**略過調參**。用於「沿用 MOT17 調出的參數」跑 MOT20（使用者已決定）。未指定時流程不變（在 Tuning Half 調參）。
- 分數摘要記錄 `benchmark`，並在略過調參時記錄參數來源，使 MOT20 的數字不會被誤讀成「針對 MOT20 調過參」。
- 錯誤訊息（找不到序列）依 benchmark 顯示正確名稱，提示的下載指令不變。
- 實測：以 `data/download_mot.sh --with-mot20` 下載 MOT20（不進版控，`data/MOT20/` 已被忽略），在 RTX 5090 以 PyTorch FP16 跑 MOT20 的 Validation Half，數字填進 README 與 `docs/evaluation.md`，並照實記錄限制。

## Capabilities

### New Capabilities

（無；都是既有能力的延伸。）

### Modified Capabilities

- `benchmark-evaluation`: 計分慣例涵蓋 MOT20；Benchmark Run 可選擇 MOT17 或 MOT20 並可使用既有的追蹤器參數；分數摘要記錄 benchmark 與參數來源。

## Impact

- 程式：`eval/run.py`（benchmark 設定、`--benchmark`、`--params`、摘要）、`eval/scoring.py` 與 `eval/tuning.py`（把 benchmark 傳給計分，新增的參數都有預設值，既有呼叫與測試不需修改）；`data/download_mot.sh` 不改。
- 測試：新增 benchmark 設定、MOT20 計分慣例（以小型假資料驗證 `non_mot_vehicle` 被當成干擾物）、`--params` 略過調參的單元測試；GPU 與資料標記的真實執行測試，MOT20 缺檔時 skip 並提示下載。
- 文件：README（評測協定、結果表新增 MOT20 一列、路線圖勾選）、`docs/evaluation.md`。
- 不新增套件；不動 api、worker、Docker、k8s、CI。
- 範圍外（使用者已決定）：MOT20 的延遲與吞吐量量測；在 MOT20 的 Tuning Half 重新調參；ONNX、TensorRT、INT8 在 MOT20 的準確度（這次只跑 PyTorch FP16）。
- 風險：
  - MOT20 約數 GB、來自 motchallenge.net，授權 CC BY-NC-SA（非商用），只存在本機並被忽略；下載失敗或網路不通時，實測部分標為未完成並回報，程式與測試部分仍可交付。
  - 沿用 MOT17 的參數，在 MOT20 的擁擠場景不一定最佳，分數可能偏低；README 必須同時寫明這一點。
  - COCO 預訓練權重未針對 MOT 微調，MOT20 的數字不可與 ByteTrack 論文並列比較。
  - 預設序列清單來自對 MOT20 資料集的了解，不是從本機驗證的；下載後以實際目錄核對，不符就以實際目錄為準並修正預設值。
