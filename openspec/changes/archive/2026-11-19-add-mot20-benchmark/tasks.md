# Tasks

## 1. 計分把 benchmark 傳給 TrackEval

- [x] 1.1 先寫失敗的測試（新檔 `tests/test_scoring_benchmark.py`）：以小型假序列（一個行人、一個 `non_mot_vehicle` 標註，追蹤結果在該標註上有一個框）呼叫 `score_sequences`，指定 `benchmark="MOT20"` 時該框不算誤報（MOTA 較高、無 FP），預設與 `benchmark="MOT17"` 時算誤報；不支援的 benchmark 值被拒絕並指名。驗證：新測試因 `score_sequences` 沒有 `benchmark` 參數而失敗，既有的 `tests/test_scoring.py` 不修改。
- [x] 1.2 實作：`eval/scoring.py` 的 `score_sequences` 末尾新增 `benchmark: str = "MOT17"` 並傳給 TrackEval 的 `BENCHMARK`，只接受 `MOT17`、`MOT20`；`eval/tuning.py` 的 `tune_parameters` 與 `_score_trial` 新增有預設值的 `benchmark` 並往下傳。驗證：1.1 通過；`tests/test_scoring.py`、`tests/test_tuning.py` 不需修改就通過。

## 2. Benchmark 設定與 `--benchmark`

- [x] 2.1 先寫失敗的測試（新檔 `tests/test_run_benchmark.py`）：`--benchmark MOT20` 的預設資料根目錄為 `data/MOT20`、預設序列為 `MOT20-01`、`02`、`03`、`05`；未指定時仍是 MOT17 的目錄與七個 FRCNN 序列；`--data-root` 與 `--sequences` 優先；找不到序列的錯誤訊息指名所選 benchmark 與缺少的序列，MOT20 另提示 `--with-mot20`，MOT17 的訊息維持原樣；`execute()` 把 benchmark 傳到計分。驗證：新測試先失敗；既有的 `tests/test_run.py`、`tests/test_run_cli.py` 不修改。
- [x] 2.2 實作：`eval/run.py` 新增 benchmark 對照表（預設資料根目錄與序列）、`BenchmarkConfig.benchmark`（末尾、預設 `MOT17`）、`--benchmark`、`--data-root` 預設改為由 benchmark 推得、錯誤訊息，並把 benchmark 傳給調參與計分。驗證：2.1 通過；既有測試不需修改就全部通過；MOT17 預設下摘要的既有鍵不變。

## 3. `--params` 略過調參與摘要

- [x] 3.1 先寫失敗的測試（加在 `tests/test_run_benchmark.py`）：指定參數檔時不呼叫 `tune_parameters`、不實體化 Tuning Half、偵測函式不收到任何屬於 Tuning Half 的影格，使用的參數等於檔案內容，`params.json` 寫出同樣的內容；摘要 `tuning` 為 `{"skipped": true, "params_file": <使用者給的路徑>}`；未指定時 `tuning` 與現在相同；摘要含 `benchmark`；參數檔不存在或欄位無效時，在任何偵測之前以非 0 結束並說明原因，不寫出執行資料夾。驗證：新測試先失敗。
- [x] 3.2 實作：`--params`、`execute()` 的略過調參分支（以既有的 `read_params` 載入，驗證在建立執行資料夾之前）、摘要的 `benchmark` 與 `tuning` 區塊。驗證：3.1 通過；既有測試不需修改就通過；`ruff check` 與 `ruff format --check` 乾淨。

## 4. 下載 MOT20 並核對

- [x] 4.1 執行 `data/download_mot.sh --with-mot20`；失敗（網路不通或下載被拒）就如實記錄訊息並停止實測部分，回報使用者決定，不編造數字，不改用其他來源。驗證：下載成功，或失敗訊息記在驗證紀錄。
- [x] 4.2 核對 `data/MOT20/train/` 的實際序列與每個序列的影格數、`seqinfo.ini` 與 `gt/gt.txt` 是否齊全；與預設序列（`MOT20-01`、`02`、`03`、`05`）不符就以實際目錄為準，修正預設值、規格敘述與設計 D1，並在驗證紀錄說明。確認 `git status` 沒有新的未追蹤資料檔（`data/MOT20/` 已被忽略）。驗證：序列與影格數列在驗證紀錄。
- [x] 4.3 新增 `data` 與 `gpu` 標記的測試 `tests/test_run_mot20_gpu.py`：在真實 GPU 與 MOT20 上以最短的序列（`MOT20-01`）跑 `python -m eval.run --benchmark MOT20 --params <MOT17 的 params.json>` 的一次短量測，檢查摘要含 `benchmark: "MOT20"`、`tuning.skipped`、三項分數為有限數字；缺 CUDA、MOT20 或 MOT17 的參數檔時 skip 並說明如何取得。驗證：`pytest -m "gpu and data" tests/test_run_mot20_gpu.py -s` 在這台機器通過。

## 5. 實測 MOT20 並記錄

- [x] 5.1 確認 GPU 閒置，並確認 MOT17 的 FP16 基準執行（`data/results/full-fp16-*`）的 `params.json` 是否相同；不同就選與 README 的 MOT17 FP16 數字對應的那次並記錄。驗證：`nvidia-smi` 輸出與參數檔比對結果記在驗證紀錄。
- [x] 5.2 以 PyTorch FP16 跑 MOT20 的 Validation Half 兩次（`mot20-fp16-3`、`mot20-fp16-4`；`-1`、`-2` 的摘要只記檔名 `params.json`，已被取代，因此改記路徑後重跑，`--benchmark MOT20 --params <MOT17 的 params.json>`），記錄耗時。驗證：兩份 `summary.json` 的分數逐位元組相同（第二次偵測來自快取，文件註明這點），且 `git status` 沒有新的未追蹤資料檔。
- [x] 5.3 整理每個序列與合併的 HOTA、MOTA、IDF1，並與 MOT17 的 FP16 列對照；分數若明顯偏低，只描述事實與可能原因（擁擠、參數未針對 MOT20、未微調的 COCO 權重），不下未驗證的結論。驗證：數字列在驗證紀錄，可由結果檔重現。

## 6. 文件與文件測試

- [x] 6.1 先寫失敗的文件測試（新檔 `tests/test_mot20_docs.py`）：README 的評測協定不再寫「MOT20 在 v0.5 之後評測」；結果表有 MOT20 的小節且數字對照記錄的結果檔（結果檔不在時 skip）；該小節註明 PyTorch FP16、參數沿用 MOT17、未微調、不可與論文並列；路線圖的 MOT20 已勾選；`docs/evaluation.md` 有 MOT20 的指令、預設序列與計分慣例差異。驗證：新測試先失敗。
- [x] 6.2 更新 README 與 `docs/evaluation.md`。驗證：6.1 通過；`tests/test_readme_*.py`、`tests/test_latency_docs.py`、`tests/test_int8_docs.py` 不需修改就通過，有因事實改變而必須更新的斷言才改，並在驗證紀錄說明。

## 7. 收尾

- [x] 7.1 `pytest` 預設全部通過、`ruff check` 與 `ruff format --check` 乾淨、`openspec validate add-mot20-benchmark --strict` 通過。驗證：貼出各指令的結尾輸出。
- [x] 7.2 驗證紀錄逐項寫明「已驗證」與「沒驗證」（尤其：只跑 PyTorch FP16、參數沿用 MOT17 而非針對 MOT20 調參、沒有延遲量測、沒有 ONNX/TensorRT/INT8 的 MOT20 數字、預設序列清單是否由實際資料確認）。驗證：文件與最後的回報一致。

## Workflow follow-up

- 提交並推送分支（規劃文件先提交一次，實作完成後再提交），合併到 main 後以 `/opsx:archive` 封存並同步主規格。
