# Tasks

## 1. 後端欄位、快取鍵與摘要

- [ ] 1.1 先寫失敗測試：`DetectorSettings` 預設 `backend` 為 `torch`、不支援的後端被拒絕並列出支援清單；再於 `tracking/detector.py` 實作。驗證：`pytest tests/test_detector.py` 通過。
- [ ] 1.2 先寫失敗測試：兩個除了 `backend` 之外都相同的 `CacheKey` 有不同的 `digest`，且後端不同時 `detect_with_cache` 不讀取對方的項目、相同時讀取；再改 `eval/detection_cache.py`。驗證：`pytest tests/test_detection_cache.py` 通過。
- [ ] 1.3 先寫失敗測試：`eval.run` 的 `summary.json` 含頂層 `backend` 且等於實際設定；再改 `eval/run.py`。驗證：`eval.run` 相關測試通過，且既有測試無回歸。

## 2. 階段接縫

- [ ] 2.1 先寫失敗測試：`StagedDetector` 組合三個階段後的輸出，與 `detect` 對同一張影像逐元素相等（以假模型）；再把 `detect` 拆成 `prepare`/`infer`/`finish` 並新增 `torch_staged_detector`。驗證：`pytest tests/test_detector.py` 通過。
- [ ] 2.2 測試 `synchronize` 在 CUDA 不可用（CPU 裝置）時是無動作、可安全呼叫。驗證：對應測試在無 GPU 環境通過。
- [ ] 2.3 在真實權重與 GPU 上，對同一批影格比對拆分前（`git stash` 或前一提交）與拆分後的偵測輸出完全相同，結果記入 `docs/evaluation.md` 的驗證紀錄。驗證：比對腳本輸出為相同；結果寫在文件中。

## 3. 量測核心

- [ ] 3.1 先寫失敗測試（假時鐘、假階段偵測器）：預熱張數不計入統計；中位數與 p95 符合 `numpy.percentile` 的定義；吞吐等於量測張數除以端到端延遲總和。再於 `eval/latency.py` 實作核心。驗證：`pytest tests/test_latency.py` 通過。
- [ ] 3.2 測試同步順序：端到端輪在每張的起訖同步、純推論輪在 `infer` 前後同步，且兩輪各自獨立。驗證：以記錄呼叫順序的假物件斷言順序。
- [ ] 3.3 測試影格來源：取自 Validation Half 依序串接、不足時循環、計時前已全部讀入記憶體。驗證：以暫存的假序列斷言取用的影格與順序，並斷言計時區間內沒有讀檔。

## 4. 指令與報告

- [ ] 4.1 先寫失敗測試：參數解析、預熱與量測張數必須為正整數（無效時結束碼非 0、指名參數、不載入模型）、MOT17 缺檔時提示 `data/download_mot.sh`、環境檢查失敗時不寫檔。再實作 `python -m eval.latency` 的 `main`。驗證：`pytest tests/test_latency_cli.py` 通過。
- [ ] 4.2 先寫失敗測試：`latency.json` 含後端、精度、輸入尺寸、權重 id 與 sha256、預熱與量測張數、軟體版本、GPU 與驅動，且與實際設定一致；`latency.md` 為繁體中文表格；結果資料夾已存在時拒絕覆蓋。再實作寫檔。驗證：同上測試檔通過。
- [ ] 4.3 確認連續執行兩次後 `git status` 沒有新的未追蹤資料檔、原始 MOT17 不變。驗證：測試以暫存目錄斷言，並在真實執行後手動檢查一次。

## 5. 真實 GPU 驗證與 baseline

- [ ] 5.1 新增 `integration` 標記的測試：在真實 GPU 與真實權重上以小張數跑完整指令，斷言端到端中位數不小於純推論中位數且結果檔齊全；預設 CI 不執行。驗證：`pytest -m integration tests/test_latency_gpu.py` 在本機 GPU 通過，預設 `pytest` 不收集它。
- [ ] 5.2 在閒置的 RTX 5090 上以預設參數（50 + 500）對 PyTorch FP16 連續量測兩次，記錄兩次的差異。驗證：`data/results/latency/` 下有兩份完整結果，差異記入文件。
- [ ] 5.3 清除舊快取後重跑一次 Benchmark Run，確認新快取鍵生效、`summary.json` 含 `backend`，且 HOTA 與既有 `full-fp16-2` 在預期的重現誤差內。驗證：新舊 `summary.md` 並排比對，差異如實記入文件。

## 6. 文件與收尾

- [ ] 6.1 在 `docs/evaluation.md`（繁體中文）新增「延遲與吞吐」章節：量測定義（兩層、預熱、p95 與吞吐的算法）、如何執行、baseline 數字與兩次量測的差異、限制（單一 GPU、可能有其他程序、不可外推），並註明舊 `summary.json` 沒有 `backend` 時視為 `torch`。驗證：新增文件測試斷言章節與關鍵說明存在，且文件中的數字與 `latency.json` 一致。
- [ ] 6.2 更新 README 路線圖：新增並勾選「延遲與吞吐 baseline」，v0.5 的其餘項目維持未勾選；更新 Status 與目錄圖。驗證：README 測試通過，且沒有宣稱未完成的項目。
- [ ] 6.3 執行完整預設測試、`ruff check .`、`openspec validate add-inference-benchmark --strict` 與 `openspec validate --specs --strict`。驗證：全部通過，預設測試數量只增不減。

## Workflow follow-up

- 提交並推送分支，確認 GitHub 上的 CI 通過後合併到 `main`。
- 以 `/opsx:archive` 封存此 change，並同步新的 `inference-benchmark` 規格與 `benchmark-evaluation` 的修改。
