# Design

## Context

動機與範圍見 proposal.md。現況：

- `tracking/detector.py` 的 `detect` 一次做完 letterbox、搬到 GPU、前向、`select_persons`（解碼與 NMS）與縮放回原圖座標，沒有可以分段計時的接縫。
- 偵測器以 `DetectFrame = Callable[[np.ndarray], np.ndarray]` 的形式被 `eval/run.py`、`tracking/run.py` 與 worker 使用；這個簽章不能變。
- `eval/detection_cache.py` 的 `CacheKey` 含權重 sha256、輸入尺寸、精度與兩個門檻，沒有後端。
- `eval/run.py` 已有環境檢查、MOT17 缺檔提示、Validation Half 的實體化與 `summary.json`；量測要沿用這些慣例。
- 開發機有一張 RTX 5090（`torch 2.7.1+cu128`，`cuda.is_available()` 為 True），但 GPU 上同時有其他容器佔用約 1.4 GB 顯存。

## Goals / Non-Goals

**Goals:**
- 在不改變偵測輸出的前提下，讓前處理、推論、後處理可以分別計時。
- 產出可重現、可比較的 baseline，且量測方法對之後的 ONNX、TensorRT 後端同樣適用。

**Non-Goals:**
- 不做後端選擇機制（環境變數、服務整合）；目前只有 `torch` 一個後端。
- 不量測追蹤器、API 或整條服務的吞吐。

## Decisions

### 1. 以階段接縫拆分 `detect`，不改輸出

在 `tracking/detector.py` 新增 `StagedDetector`（凍結資料類別），含 `prepare`、`infer`、`finish` 三個函式與 `synchronize`；`detect` 改為依序呼叫這三個階段，輸出位元組不變。新增 `torch_staged_detector(detector)` 提供 PyTorch 的實作，`load_default_detect_frame` 仍回傳 `DetectFrame`。

- **為何**：量測要能只計時推論，而後續後端只需實作同樣三個階段，量測程式不必改。
- **替代方案**：在量測程式裡複製 `detect` 的內容（會與正式路徑漂移，量到的不是實際跑的東西）；用 monkeypatch 或鉤子插入計時（脆弱，且 GPU 同步點難以控制）；只量端到端再扣掉估計值（不準）。
- 既有 `tests/test_detector.py` 與新增的「階段組合等於 `detect`」測試共同保證行為不變。

### 2. 兩次獨立的量測，不在同一輪內分段同步

先跑一輪量測端到端（只在每張的起訖同步），再跑一輪只計時 `infer`（`prepare` 在計時外先做好，`infer` 前後同步）。

- **為何**：在單一輪內於階段之間插入同步會打斷 GPU 管線，使端到端數字偏高、失真。
- **替代方案**：單輪、每階段都同步（較快，但端到端不再代表真實使用）。
- 代價是量測時間加倍；500 張的規模下可接受。

### 3. 計時與統計的定義

- 計時用 `time.perf_counter`，起點前與終點前都呼叫 `synchronize`；時鐘與同步函式可注入，測試用假時鐘。
- 影格先全部以 `cv2.imread` 讀入記憶體再開始計時。
- p95 與中位數用 `numpy.percentile`（線性內插）；吞吐 = 量測張數 ÷ 端到端延遲總和（秒）。這些定義寫在 `docs/evaluation.md`，使數字可被他人重算。

### 4. 影格來源與張數

取各預設 Sequence 的 Validation Half 依序串接，循環使用至預熱 + 量測張數；預設 50 + 500。沿用 `split_sequence` 與 `materialize_half` 的切分，避免另寫一套切分規則。

### 5. 後端欄位與快取鍵

`DetectorSettings` 新增 `backend: str = "torch"`，並以 `SUPPORTED_BACKENDS = ("torch",)` 驗證；`CacheKey` 新增 `backend` 欄位，`summary.json` 新增頂層 `backend`。

- **為何**：快取鍵不含後端時，換後端會悄悄讀到舊後端的偵測，HOTA 對照就失去意義。
- **替代方案**：只有非 `torch` 後端才把欄位放進鍵，以保留既有快取（鍵的內容出現特例，之後難以推理）。選擇讓既有快取失效一次，換取規則單純。
- 本 change 不加 `--backend` 旗標，因為只有一個選項；後續 change 擴充 `SUPPORTED_BACKENDS` 時再加。

### 6. 結果位置與格式

寫到 `data/results/latency/<run-name>/latency.json` 與 `latency.md`（繁體中文表格，風格同 `summary.md`）。放在獨立子資料夾，避免與 `eval.run` 要求「run 資料夾不得預先存在」的規則互相衝突。`data/results` 已被版本控制忽略。

## Risks / Trade-offs

- [GPU 上有其他程序，延遲數字被干擾] → 文件要求在閒置的 GPU 上量測；baseline 連續量測兩次並記錄差異；結果只宣稱代表這台機器。
- [快取鍵改變使下一次 Benchmark Run 重新偵測] → 屬一次性成本，在 proposal 與文件註明。
- [`detect` 拆分引入行為差異] → 以既有測試、階段組合等價測試，以及在真實權重上比對拆分前後的偵測輸出（任務 2.3）把關。
- [純推論數字依賴同步正確] → 以假時鐘測試同步呼叫的順序；真實 GPU 的整合測試確認端到端不小於純推論。
- [baseline 只在一張 GPU、一組驅動上量得] → 結果檔記錄 GPU 與驅動，文件不外推到其他硬體。

## Migration Plan

無資料遷移。既有 `data/results/cache` 的項目因鍵改變而被忽略，可手動清除；舊的 `summary.json` 沒有 `backend` 欄位，閱讀時視為 `torch`（文件註明）。回滾即還原此 change 的提交。
