# Proposal

## Why

openmot-serve 目前只有設計文件（README、CONTEXT.md、ADR-0001），沒有任何可執行的程式碼。後續所有階段（v0.2 非同步服務、v0.3 Kubernetes、v0.4 觀測、v0.5 INT8 量化）都建立在同一件事上：一條能對 MOT17 影像序列產出追蹤結果、並能用 HOTA 量化品質的管線。

沒有這條管線與可重現的基準分數，v0.5 的「量化前後比較」就沒有基準線（baseline）可比；README 的「Results」表格也只能一直是 TBD。現在先把管線與評測做出來，是為了讓後面每個階段都有可驗證的起點。

## What Changes

- 新增 `tracking/` 套件：以 YOLOX（COCO 預訓練權重，見 ADR-0001）偵測人物，再以 ByteTrack 串成 track，輸出 MOTChallenge 格式的結果。
- 新增執行環境驗證：確認 RTX 5090 上 PyTorch 能使用 CUDA 12.8+，並能實際跑一次 YOLOX 前向推論。
- 新增 `eval/` 套件：把 MOT17 每個訓練序列切成 Tuning Half 與 Validation Half，在 Tuning Half 上選追蹤器參數，只在 Validation Half 上用 TrackEval 報告 HOTA、MOTA、IDF1。
- 新增以 `uv` 管理的 Python 專案（`pyproject.toml`、鎖定檔），並用一個指令重跑整個 Benchmark Run。
- 把第一次的結果填進 README 的「Tracking accuracy」表格（僅 FP16 那一列；INT8 留給 v0.5）。
- 不包含：FastAPI、Celery、Redis、Docker、Kubernetes、監控、INT8 量化、MOT20（皆屬後續階段）。

## Capabilities

### New Capabilities
- `runtime-environment`: 驗證 GPU、CUDA 與 PyTorch 在目標機器上可用，並以指令回報結果。
- `sequence-tracking`: 將一個 Sequence 偵測並追蹤，輸出 MOTChallenge 格式的 Result。
- `benchmark-evaluation`: 切分 Tuning Half 與 Validation Half，並以 TrackEval 產生可重現的 HOTA、MOTA、IDF1 分數。

### Modified Capabilities

（無。`openspec/specs/` 目前為空，這次全是新增。）

## Impact

- **新增程式碼**：`tracking/`、`eval/`、`tests/`，以及根目錄的 `pyproject.toml` 與 `uv.lock`。
- **新增依賴**：PyTorch（已在系統環境確認為 2.7.1+cu128）、YOLOX、TrackEval、numpy、scipy、opencv。ByteTrack 的追蹤器邏輯以 MIT 授權精簡移植，並保留授權聲明。
- **外部資料**：MOT17 由既有的 `data/download_mot.sh` 下載（CC BY-NC-SA 3.0，不進版控）；YOLOX 權重已被 `.gitignore` 排除。
- **文件**：README 的「Results」表格與路線圖 v0.1 勾選；新增評測流程說明。
- **風險**：COCO 權重未針對 MOT17 微調，絕對分數預期低於 ByteTrack 論文數字，這是刻意的（授權與可重現性優先），需在結果旁明確標註。
