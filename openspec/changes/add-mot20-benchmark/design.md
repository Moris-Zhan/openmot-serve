# Design

## Context

動機見 proposal。現況（已在程式中確認）：

- `eval/run.py` 的 `BenchmarkConfig` 是 frozen dataclass，欄位 `data_root, results_root, run_name, precision, weights, device` 為位置參數，後面有預設值的 `sequences, backend, engine`。測試以關鍵字與位置參數建構它。`DEFAULT_SEQUENCES` 是七個 `MOT17-XX-FRCNN`；`--data-root` 預設 `data/MOT17`。
- `execute()` 的流程是：檢查序列 → 環境檢查 → 解析產物 → 建立執行資料夾 → `_tune()` 實體化每個序列的 Tuning Half 並偵測、跑 `tune_parameters` → 寫 `params.json` → `_validate()` → 寫 `summary.json` 與 `summary.md`。
- `eval/scoring.py` 的 `score_sequences(sequence_dirs, result_files, workspace)` 把 `"BENCHMARK": "MOT17"` 寫死；`eval/tuning.py` 的 `_score_trial` 也呼叫它。
- 已確認 TrackEval（`eval/vendor`）只在 `BENCHMARK == "MOT20"` 時把 `non_mot_vehicle` 加進干擾物類別；其餘計分邏輯與 MOT17 相同。
- `eval/tuning.py` 已有 `write_params` 與 `read_params`（`read_params` 會檢查欄位集合），每次執行把 `params.json` 寫在執行資料夾。
- 偵測快取以序列名稱與切半位置為鍵（`f"{name}-tuning"`），MOT20 的序列名稱與 MOT17 不同，不會互相碰撞；快取的鍵另含精度、後端與權重 sha256。
- `.gitignore` 已有 `data/MOT20/`；`data/download_mot.sh --with-mot20` 已存在，但它對 MOT20 只檢查資料夾是否存在，不檢查序列是否完整。
- 這台機器目前沒有 `data/MOT20`。

## Goals / Non-Goals

**Goals:**
- 讓同一個評測指令能以正確的 MOT20 計分慣例跑 MOT20，並且在 MOT17 預設下輸出與現在逐位元組相同。
- 沿用 MOT17 調出的參數跑 MOT20（使用者決定），並讓結果的讀者能看出這點。
- 在 RTX 5090 上實跑 PyTorch FP16 的 MOT20 Validation Half，把數字與限制寫進文件。

**Non-Goals:**
- MOT20 的延遲與吞吐量量測；在 MOT20 重新調參；ONNX、TensorRT、INT8 的 MOT20 準確度。
- 在 MOT20 上微調偵測器；與 ByteTrack 論文的數字並列。
- 改 `data/download_mot.sh`（見 D6）。

## Decisions

### D1：以一個 benchmark 名稱決定預設值與計分慣例

新增 `--benchmark {MOT17,MOT20}`，預設 `MOT17`。一個小的對照表把 benchmark 對應到預設資料根目錄與預設序列：MOT17 為現有的目錄與七個 FRCNN 序列，MOT20 為 `data/MOT20` 與 `MOT20-01`、`02`、`03`、`05`。`--data-root`、`--sequences` 明確指定時優先。`BenchmarkConfig` 末尾新增有預設值的欄位 `benchmark: str = "MOT17"`，所以既有的建構方式與測試不需修改。`--data-root` 的預設值改為由 benchmark 推得（parser 內以 `None` 代表未指定）。

替代方案：從序列名稱推斷 benchmark（`MOT20-` 開頭）。拒絕：明確的旗標比推斷可預測，且使用者以 `--sequences` 指定自訂清單時推斷會出錯。

### D2：計分把 benchmark 傳給 TrackEval，新參數都有預設值

`score_sequences` 末尾新增 `benchmark: str = "MOT17"`，直接傳給 TrackEval 的 `BENCHMARK`；`tune_parameters` 與 `_score_trial` 同樣新增有預設值的 `benchmark` 並往下傳。這是 MOT20 計分慣例（`non_mot_vehicle` 干擾物）的唯一入口。MOT17 預設下傳入的值與現在寫死的相同，因此輸出不變。不接受 `MOT17`、`MOT20` 之外的值（TrackEval 還支援 MOT16、MOT15，但專案沒有這些資料，不開放）。

### D3：`--params` 略過調參，且不碰 Tuning Half

指定 `--params <檔案>` 時，用既有的 `read_params` 載入（無效就在任何偵測開始之前失敗，不寫執行資料夾），`execute()` 略過 `_tune()`：不實體化 Tuning Half、不對 Tuning Half 偵測、不跑 `tune_parameters`。仍然寫 `params.json`（內容是使用的參數）。摘要的 `tuning` 區塊改記 `{"skipped": true, "params_file": "<檔名>"}`，沒有 `combined_hota` 與 `combinations`；未指定時 `tuning` 區塊與現在相同。

驗證要在建立執行資料夾之前做，與現有「不支援的組合在偵測之前失敗」的做法一致。

替代方案：
- 自動去找 MOT17 最近一次的 `params.json`：隱含的來源讓結果難以重現，拒絕，來源一律由使用者以路徑明講。
- 把 MOT17 的參數寫成程式內常數：參數是調參的產物，不是常數；MOT17 重調後常數就會過時。

### D4：摘要多記 `benchmark`，並明確標示未調參

`summary.json` 新增頂層 `benchmark`；`tuning.skipped` 與 `tuning.params_file` 讓 README 與文件能據實寫「參數沿用 MOT17、未針對 MOT20 調整」。`summary.md` 的表格格式不變（序列名稱已含 benchmark）。MOT17 預設下，既有鍵的內容不變，只多一個 `benchmark: "MOT17"`。

### D5：實測用 PyTorch FP16，沿用 MOT17 那次的參數

實測以 PyTorch FP16（與 README 結果表的基準列相同）跑 MOT20 四個序列的 Validation Half，參數取自 MOT17 的 FP16 基準執行（`data/results/full-fp16-*` 之一；實作時先確認這幾次執行寫出的 `params.json` 是否相同，不同就選用 README MOT17 FP16 數字所對應的那次，並在文件寫明）。執行名稱 `mot20-fp16-<序號>`，結果在被忽略的 `data/results/`，跑兩次確認逐位元組相同的結果（偵測有快取，第二次不重新推論；因此第二次主要驗證計分與追蹤的決定性，這點在文件註明，不宣稱重跑了推論）。

### D6：`download_mot.sh` 不改，下載後自行核對

腳本的 MOT20 分支只檢查資料夾存在，對「中斷的下載」不夠嚴格，但這次下載是一次性的、由我當場確認；改腳本是另一個範圍。實作時下載完成後，以實際的 `train/` 目錄核對預設序列清單，並列出每個序列的影格數，寫進驗證紀錄。若實際序列與預設不同，就修正預設值與規格敘述，並在驗證紀錄說明。

### D7：文件與結果的寫法

README 的評測協定把「MOT20 在 v0.5 之後評測」改為已評測及其限制；結果表新增一個 MOT20 的小節或列，欄位與 MOT17 相同，標題與註解寫明「PyTorch FP16、參數沿用 MOT17、COCO 預訓練權重未微調、不可與論文並列、擁擠場景分數可能偏低」。路線圖勾選 MOT20。`docs/evaluation.md` 補 MOT20 的指令、預設序列、計分慣例差異與限制。數字由文件測試對照結果檔驗證（沿用既有文件測試的做法，結果檔不在時 skip）。

## Risks / Trade-offs

- **MOT20 下載失敗或網路不通** → 程式與測試仍可交付；實測與 README 的 MOT20 數字標為未完成並回報，由使用者決定。不編造數字。
- **預設序列清單與實際資料不符** → 下載後核對（D6），以實際目錄為準。
- **沿用 MOT17 參數在擁擠場景偏低** → 摘要與文件寫明（D4、D7）；不把它說成 MOT20 的最佳結果。
- **MOT20 序列大（約 4400 張 Validation Half 影格，1080p）** → 這台機器 GPU 閒置，偵測有快取；預期分鐘級。真實耗時照實記錄。
- **計分慣例差異是靜默的** → 以小型假資料的測試固定「同一份資料，MOT20 把 `non_mot_vehicle` 當干擾物、MOT17 當誤報」，防止日後有人把 benchmark 寫回死值。
- **`--data-root` 預設值改成由 benchmark 推得** → 保持 MOT17 預設的輸出與路徑不變，由既有的 `tests/test_run_cli.py` 與新的預設值測試把關。

## Migration Plan

無資料遷移。新增的旗標與欄位都有預設值，不指定時行為不變。要回復，還原程式與文件即可；MOT20 資料與結果都在被忽略的目錄。
