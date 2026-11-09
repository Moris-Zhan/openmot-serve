# Design

## Context

動機見 proposal.md 的 Why。現況（已唯讀確認）：

- 專案目前沒有任何 Python 程式碼，只有文件與 `data/download_mot.sh`；`data/results/`、`*.pt`、`*.pth`、`*.onnx`、`*.engine` 已被 `.gitignore` 排除。
- 目標機器有 RTX 5090（32 GB，driver 580.126）；系統 Python 3.10 內的 PyTorch 為 2.7.1+cu128，`cuda.is_available()` 為 True。系統環境沒有 opencv、yolox、TrackEval；numpy 為 2.2.6。
- 領域詞彙以 CONTEXT.md 為準（Sequence、Frame、Detection、Track、Identity、Result、Tuning Half、Validation Half、Benchmark Run）；偵測器選擇見 ADR-0001（YOLOX，Apache-2.0）。
- 本設計的產出要能在 v0.2 被 Celery worker 直接呼叫，所以追蹤要設計成函式庫，而不是只能從命令列使用的腳本。

## Goals / Non-Goals

**Goals:**
- 一個可重現的基準線：同樣的資料、權重、參數，追蹤階段產生逐位元組相同的 Result。
- 切分與調參的流程在結構上就無法碰到 Validation Half，而不是靠自律。
- 追蹤核心是純函式加不可變資料，I/O 只在邊界，方便 v0.2 重用與測試。

**Non-Goals:**
- 不做影片檔輸入（v0.1 只收影格資料夾；影片轉影格留給 v0.2）。
- 不微調 YOLOX、不加 ReID、不做相機運動補償，也不做 MOT20 與 INT8。
- 不追求論文分數；目標是「可重現且公平比較」。

## Decisions

### D1：單一 uv 專案，Python 3.11，PyTorch 走 cu128 索引
`pyproject.toml` + `uv.lock`，套件 `tracking/`、`eval/`、`tests/`（對應 README 的版面）。選 3.11 是因為內建 `tomllib`，可用 TOML 當設定檔而不必多裝套件。PyTorch 以 uv 的明確索引從 `download.pytorch.org/whl/cu128` 安裝，確保拿到含 Blackwell（sm_120）核心的版本。
*替代方案*：沿用系統 Python 3.10 與其 site-packages——環境不可重現，且 numpy 2.x 會和 TrackEval 衝突，放棄。

*實作時的調整（依使用者要求）*：開發與測試環境改為 conda 環境 `openmot`（Python 3.11），但套件仍以 `uv pip install --python /root/miniconda3/envs/openmot/bin/python` 安裝，`pyproject.toml` 與 cu128 索引設定不變。本文件與 tasks.md 中的 `uv run <指令>` 等同於 `conda run -n openmot <指令>`；因此不使用 `uv sync`，也不產生 `uv.lock`（proposal 的 Impact 原先提到的 `uv.lock` 在此改由 `pyproject.toml` 中固定的版本與 commit 取代）。

### D2：偵測用 YOLOX-X（COCO 權重），只留人物，偵測信心下限設低
權重從官方 release 固定版本下載，驗證 SHA-256 後放在已被忽略的 `models/`。載入時先用 `weights_only=True`；只有在雜湊驗證通過後才允許退回 `False`（torch 2.6 起預設值改變）。輸入尺寸預設採用 ByteTrack 在 MOT17 的 800×1440，可設定；NMS 與信心下限（0.1）固定寫入設定，使追蹤階段可以只靠過濾就套用任何 ≥0.1 的門檻。FP16 以 autocast 實現，精度記錄在中繼檔。
*替代方案*：Ultralytics YOLO——AGPL，已被 ADR-0001 否決。YOLOX-L/M 保留為設定選項，不是預設。

### D3：自行精簡移植 ByteTrack，用 scipy 做匹配
移植 `BYTETracker` 的核心：恆速 Kalman 濾波、高低信心兩階段關聯、IoU 匹配、Track 保留幀數。匹配改用 `scipy.optimize.linear_sum_assignment`，避免原專案對 `lap`、`cython_bbox` 的編譯依賴與 `np.float` 這類在 numpy 2 已移除的寫法。檔頭保留 MIT 授權聲明。以「狀態 + 本幀 Detection → 新狀態 + 輸出」的純函式介面實作，狀態用凍結的 dataclass，內部 numpy 陣列視為唯讀。
*實作時的調整（授權）*：ByteTrack 的 `kalman_filter.py` 承襲自 DeepSORT（GPL-3.0），與 ADR-0001 避開 copyleft 的原則衝突，因此 Kalman 濾波器不移植該檔，改依教科書的恆速模型公式自行撰寫（`tracking/kalman.py`），只沿用 ByteTrack 公開的雜訊權重（位置 1/20、速度 1/160）。`byte_tracker.py` 與 `matching.py` 屬 ByteTrack 本身（MIT），照設計移植；授權全文在 `tracking/licenses/ByteTrack-LICENSE`。另有兩處刻意偏離上游：Identity 計數器改為每次執行獨立（從 1 起、不重用），以及超過保留幀數的遺失 Track 立即丟棄，不再多參與一輪關聯。
*替代方案*：`boxmot`、Ultralytics tracker——皆為 AGPL，同 ADR-0001 的理由不採用；直接 vendor 原 repo——相依舊 numpy，需要大量修補。

### D4：Half 以「實體化的資料夾」表示，讓 TrackEval 與追蹤都把它當一般 Sequence
對每個 Sequence 與每個 Half，在 `data/results/<run>/halves/<seq>/<half>/` 下建立：重新編號為 `000001.jpg…` 的影格符號連結（symlink）、裁切並重新編號後的 `gt/gt.txt`、以及對應長度的 `seqinfo.ini`。原始 MOT17 目錄完全不動。如此追蹤階段與 TrackEval 都不需要知道「半段」的存在，也自然滿足 spec 對 Frame 從 1 起算的要求。切分算術（floor(N/2)）放在一個純函式中，單獨測試。
MOT17 影像來源固定使用 `-FRCNN` 版本的資料夾：三個偵測器版本的影格與標註相同，而本專案使用自己的偵測結果，不使用 MOT17 附帶的 `det.txt`。
*替代方案*：評分時才過濾 Frame 範圍——需要改 TrackEval 的行為，且「Validation 資料在調參時不可被讀取」更難在結構上保證。

### D5：先偵測並快取，再離線掃描追蹤參數
偵測是最貴的步驟，追蹤參數掃描很便宜。因此流程分階段：
1. 只實體化 **Tuning Half** → 偵測並將 Detection 快取成 `.npz`（快取鍵含權重雜湊、輸入尺寸、精度、NMS 設定）。
2. 在快取上對參數網格（高信心門檻 × 匹配門檻 × Track 保留幀數，約 24 組）離線追蹤，以 Tuning Half 的合併 HOTA 選最佳組合，寫入 `params.json`。
3. 參數確定後才實體化 **Validation Half** → 偵測 → 以確定參數追蹤 → TrackEval 計分 → 寫 `summary.json`。
Validation Half 在第 2 步結束前根本不存在於磁碟上，「調參不碰 Validation」因此由流程順序保證，並由測試驗證。

### D6：計分使用 TrackEval 的 MOT17 行人設定，並以「GT 當預測」做端到端自我檢查
以固定 commit 的 TrackEval 計 HOTA、MOTA、IDF1。另加一個檢查：把裁切後的 GT 直接當成預測送進整條計分流程，三項分數都應接近 100；這能一次驗出切分、重新編號、格式與 TrackEval 設定是否有誤。

### D7：程式風格與測試
依專案慣例：函式優先於類別、凍結 dataclass、所有函式簽名加型別註記。測試用 pytest，並以標記區分：預設只跑不需 GPU 與不需 MOT17 的測試（切分算術、MOTChallenge 格式、合成 Detection 上的追蹤行為、環境檢查的判斷邏輯、GT 裁切）；`gpu` 標記跑 YOLOX 前向冒煙測試；`data` 標記跑 GT 當預測的自我檢查與完整 Benchmark Run。測試資料一律用合成的，不放 MOT17（授權不允許進版控）。

### D8：一個指令的 Benchmark Run
`uv run python -m eval.run`（可帶 `--precision fp16`）依序做環境檢查 → 切分與調參 → 驗證計分 → 寫出 `summary.json` 與可貼進 README 的 Markdown 表格。摘要記錄權重雜湊、精度、參數、軟體版本、GPU 型號，供 v0.5 公平比較。

## Risks / Trade-offs

- **COCO 權重未針對 MOT17 微調，分數會低於 ByteTrack 論文** → 在 README 結果旁明確標註「COCO 預訓練、未微調」，且不與論文數字並列比較；v0.5 比較的是同一基準線的前後差異。
- **TrackEval 與 numpy 2.x 不相容（上游使用已移除的 `np.float`/`np.int`）** → 已驗證確實不相容（19 個檔案共 81 處，MOT17 必用的三個模組都中）。決定：內嵌修補版放在 `eval/vendor/trackeval/`，只複製評測需要的 11 個檔案並把別名替換成內建型別，來源 commit 與修改內容記在該目錄的 `NOTICE.md`。在 numpy 2.4.6 上以合成資料驗證：GT 當預測得 HOTA／MOTA／IDF1 皆為 100.00；中途互換 Identity 的對照組為 74.54／98.67／66.67。
- **YOLOX 與 torch 2.7 的相容性（`weights_only` 預設、舊版 API）** → 只匯入需要的模組；固定 commit；GPU 冒煙測試作為早期警報。
- **FP16 推論在不同 GPU 或驅動上可能有微小差異** → 偵測結果快取並記錄於摘要；可重現性的保證範圍限定為「相同偵測輸入 → 相同追蹤輸出」，spec 也是這樣寫的。
- **Validation Half 從冷啟動開始追蹤（沒有前半段的 Track 狀態）** → 這是 Half 被視為獨立 Sequence 的直接後果，對所有後續比較一視同仁，接受並在文件中說明。
- **參數網格太小或過擬合 Tuning Half** → 網格刻意保持小；Validation Half 只用一次，不回頭調參。
- **大量下載（PyTorch cu128 wheel、MOT17 約數 GB）** → 在任務中分開列出，不在測試流程裡自動觸發。

## Migration Plan

全新程式碼，無資料遷移。回退方式為還原本變更的 commit。`pyproject.toml` 與 `uv.lock` 是新增檔案，不影響既有檔案。

## Open Questions

- YOLOX 的預設型號（X 或 L）與輸入尺寸：先以 X、800×1440 為基準，若速度成為問題再於後續調整，不影響 spec。
- README 是否要附每個 Sequence 的分數，還是只放合併分數：摘要檔兩者都有，README 呈現方式可之後再定。
