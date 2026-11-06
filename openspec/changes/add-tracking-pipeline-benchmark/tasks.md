# Tasks

## 1. 專案骨架與環境檢查（runtime-environment）

- [x] 1.1 建立 `pyproject.toml`（Python 3.11、uv、PyTorch 走 cu128 索引、pytest 與 `gpu`／`data` 標記），執行 `uv sync` 並確認 `uv run python -c "import torch; print(torch.version.cuda, torch.cuda.is_available())"` 印出 `12.8 True`
- [x] 1.2 先寫失敗的測試，再實作環境檢查的判斷邏輯（CUDA 版本比較、GPU 與 CPU 運算結果比對、錯誤分類）；驗證 `uv run pytest tests/test_env_check.py` 全部通過，且涵蓋「CUDA 不可用」「CUDA 12.4 過低」「GPU 運算失敗」三個失敗情境
- [x] 1.3 實作 `python -m tracking.env_check`（回報 GPU 名稱、驅動版本、PyTorch 版本、CUDA 版本、是否可用，並實際在 GPU 上做矩陣運算）；驗證在本機執行結束碼為 0 並印出五項資訊，在 `CUDA_VISIBLE_DEVICES=""` 下結束碼非 0

## 2. 相依套件風險驗證（先確認再寫程式）

- [x] 2.1 固定 TrackEval 的 commit 並加入相依，以一組很小的合成資料執行「GT 當預測」，驗證能在鎖定的 numpy 上跑出 HOTA／MOTA／IDF1 接近 100；若因 `np.float` 等問題失敗，改採最小修補或獨立 uv 環境，並把結果與決定寫回 design.md 的 Risks
- [x] 2.2 固定 YOLOX 的 commit 並加入相依，下載 `yolox_x.pth`，把 SHA-256 寫進程式常數；驗證以 `weights_only=True` 載入成功（失敗則記錄並僅在雜湊通過後退回），且 `uv run pytest -m gpu tests/test_yolox_smoke.py` 能在 FP16 下對 800×1440 的隨機張量完成一次前向推論並得到預期輸出形狀

## 3. 序列追蹤（sequence-tracking）

- [x] 3.1 先寫測試再實作 MOTChallenge 結果的格式化與解析函式；驗證測試涵蓋「10 個欄位」「Frame 與 Identity 為正整數」「Frame 從 1 起算」，並以 TrackEval 讀取產出檔不丟棄任何一行
- [x] 3.2 先寫測試再移植 ByteTrack 核心（恆速 Kalman 濾波、高低信心兩階段關聯、IoU 匹配改用 scipy、Track 保留幀數；檔頭保留 MIT 授權聲明）；驗證合成 Detection 上的測試涵蓋 Identity 持續、短暫遮擋後沿用、Track 終止後 Identity 不被重用、相同輸入兩次輸出逐位元組相同
- [x] 3.3 實作 TOML 設定載入（偵測信心門檻、匹配門檻、Track 保留幀數與預設值）與中繼檔寫入（參數、權重雜湊、推論精度）；驗證測試涵蓋「自訂參數被使用且被記錄」與「預設值被記錄」
- [x] 3.4 實作偵測器封裝（權重雜湊驗證、letterbox 前處理、FP16、NMS、信心下限 0.1、只保留人物類別）；驗證「只保留人物」的單元測試通過，且 `-m gpu` 測試對一張合成影像能回傳形狀正確的 Detection 陣列
- [ ] 3.5 實作 `track_sequence(frames_dir, config)` 與命令列 `python -m tracking.run`，輸出先寫暫存檔再改名；驗證測試涵蓋「有效資料夾產生 Result」與「資料夾不存在、為空、含無法讀取影格時結束碼非 0 且不留下 Result 檔」
- [ ] 3.6 在 `docs/tracking.md` 以繁體中文說明追蹤指令與設定檔；驗證文件中的範例指令能照字面執行（以合成影格資料夾）

## 4. 評測（benchmark-evaluation）

- [ ] 4.1 先寫測試再實作切分函式；驗證以 MOT17 七個訓練序列的長度做參數化測試，Tuning Half 與 Validation Half 不重疊且合起來涵蓋全部 Frame，並涵蓋 600 與 525 兩個具體案例
- [ ] 4.2 先寫測試再實作 Half 實體化（影格符號連結重新編號、GT 裁切並重新編號、`seqinfo.ini`）；驗證測試涵蓋「Frame 從 1 起算」「GT 只含範圍內的列」「原始 MOT17 目錄內容與修改時間不變」
- [ ] 4.3 先寫測試再實作 TrackEval 轉接層（MOT17 行人設定、取出各序列與合併的 HOTA／MOTA／IDF1）；驗證「GT 當預測」在合成資料上三項分數接近 100，「標為不列入評分的 GT」不計誤報與漏報，且超出範圍的 Frame 不影響分數
- [ ] 4.4 先寫測試再實作 Detection 快取（`.npz`，鍵含權重雜湊、輸入尺寸、精度、NMS 設定）；驗證相同鍵命中、任一鍵值改變即未命中
- [ ] 4.5 先寫測試再實作參數調校（在 Tuning Half 的快取上掃描約 24 組參數，以合併 HOTA 選最佳並寫 `params.json`）；驗證調校結束後磁碟上不存在任何 Validation Half 目錄，且調校函式只接受 Tuning Half 的型別
- [ ] 4.6 實作單一指令 `uv run python -m eval.run`（環境檢查 → Tuning Half 偵測與調校 → Validation Half 偵測、追蹤、計分 → 寫 `summary.json` 與 Markdown 表格）；驗證缺少 MOT17 時結束碼非 0 並提示 `data/download_mot.sh`，並在合成資料根目錄上連跑兩次後 `git status --porcelain` 無新增項目、原始資料不變
- [ ] 4.7 確認 `summary.json` 記錄權重雜湊、推論精度、最終參數、軟體版本與 GPU 型號；驗證測試比對摘要內容與該次實際設定一致
- [ ] 4.8 在 `docs/evaluation.md` 以繁體中文說明切分規則、調參流程與一個指令的使用方式；驗證文件中的指令能照字面執行

## 5. 第一次真實 Benchmark Run 與整合驗證

- [ ] 5.1 執行 `./data/download_mot.sh` 下載 MOT17；驗證 `data/MOT17/train` 下有 7 個序列各 3 個偵測器版本，且 `git status` 未出現資料檔
- [ ] 5.2 在真實 MOT17 的 Validation Half 上執行「GT 當預測」自我檢查（`pytest -m data`）；驗證 HOTA、MOTA、IDF1 皆接近 100，作為切分、重新編號與計分流程正確的證據
- [ ] 5.3 執行完整的 `uv run python -m eval.run --precision fp16`；驗證 `summary.json` 與 Markdown 表格產生，並以快取的 Detection 重跑一次，兩次的追蹤輸出逐位元組相同、分數完全相同
- [ ] 5.4 把實際分數填入 README 的「Tracking accuracy」FP16 那一列（INT8 維持 TBD），在表格旁標註「COCO 預訓練權重，未針對 MOT17 微調」，並勾選路線圖 v0.1；驗證 README 的數字與 `summary.json` 逐項相符
- [ ] 5.5 執行預設的完整測試（不含 `gpu`、`data`）與 `openspec validate add-tracking-pipeline-benchmark --strict`；驗證兩者皆通過

## Workflow follow-up

- 審閱完成後歸檔此變更（`/opsx:archive`），並確認三個 capability 已進入 `openspec/specs/`。
- 歸檔後更新 README 頂端的 Status 說明（目前寫著 planning、pre-v0.1）。
