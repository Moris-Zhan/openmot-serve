# Design

## Context

動機見 proposal.md 的 Why。現況（已唯讀確認）：

- 沒有 `.github/`、沒有 lint、型別檢查或 pre-commit 設定；`pyproject.toml` 只有 pytest 設定，`dev` extras 是 `pytest`、`httpx`、`pyyaml`。遠端是 `github.com/Moris-Zhan/openmot-serve`。
- 預設測試（`-m 'not gpu and not data and not integration'`）在隱藏 GPU（`CUDA_VISIBLE_DEVICES=""`）時 569 項全過，所以不需要 GPU runner；其中 `tests/test_detector.py` 與 `tests/test_yolox_smoke.py` 會匯入 `torch`，所以 CI 需要安裝 PyTorch（CPU 版即可）。
- `pyproject.toml` 把 `torch` 與 `torchvision` 釘在 2.7.1，並以 uv 的 `explicit` 索引指向 cu128。CUDA 版的 PyPI wheel 連同 NVIDIA 相依套件有數 GB，不適合每次 CI 安裝。
- ruff（0.8.4）在寬鬆設定下的結果：預設規則只有 2 處違規（`E731` 一處、`F401` 一處）；加上 `E,F,I,B,UP` 與行寬 120 後，是 `E501` 18 處、`I001` 10 處、`B008` 1 處，其餘兩處同上。`ruff format --check` 會要求 83 個檔案中的 41 個重新排版。
- 映像：api 約 0.76 GB（不含 torch）；worker 約 11.4 GB（cu128 torch）。GitHub 託管 runner 的可用磁碟有限，worker 建置需要先清理。
- Kubernetes 清單測試在缺少 `kustomize` 或 `kubeconform` 時會 skip（這是刻意設計，讓本機沒有工具也能跑），所以在 CI 裡必須確保工具存在，否則 50 項測試會靜默略過。

## Goals / Non-Goals

**Goals:**
- 每次 push 與 pull request 得到可信的 lint、測試與 api 映像建置結果。
- main 上發佈 api 與 worker 映像，供日後的叢集部署取用。
- 每個檢查都能在本機以文件中的指令重現。

**Non-Goals:**
- 不部署到叢集、不做版本發佈、不做 `ruff format --check`、不加 mypy、不做 Python 版本矩陣、不使用 GPU runner。

## Decisions

### D1：一個工作流程檔，四個工作
`.github/workflows/ci.yml`：`lint`、`test`、`build-api` 在每次 push 與 pull request 平行執行；`publish` 只在 push 到 `main` 時執行，`needs: [lint, test, build-api]`。拆成獨立工作使失敗原因一目了然，也讓 lint 很快失敗而不必等測試。

### D2：觸發與取消
`on: push`（所有分支）與 `on: pull_request`；不使用 `pull_request_target`（它會讓 fork 的程式碼在有機密的環境執行）。`concurrency` 以 `ci-${{ github.ref }}` 分組並 `cancel-in-progress: true`，但 `main` 上不取消進行中的發佈，以免發佈到一半被中止。

### D3：最小權限
工作流程層級 `permissions: contents: read`。只有 `publish` 工作額外加 `packages: write`。沒有其他機密，登入 GHCR 用內建的 `GITHUB_TOKEN`。

### D4：測試環境用 CPU 版 PyTorch
Python 3.11（與 Dockerfile 一致，`requires-python` 是 `>=3.11,<3.13`，只測一個版本）。步驟：先以 `--index-url https://download.pytorch.org/whl/cpu` 安裝 `torch==2.7.1` 與 `torchvision==0.22.1`，再 `pip install -e ".[dev,service]"`；已滿足的 `torch==2.7.1` 不會被重新解析成 CUDA 版。以 `setup-python` 的 pip 快取加速。
*替代方案*：在 CI 用 uv 與專案的 cu128 索引——會下載數 GB 的 CUDA 套件，沒有好處。

### D5：ruff 設定放在 `pyproject.toml`
`[tool.ruff]` 行寬 120、排除 `eval/vendor`（第三方原始碼）；`[tool.ruff.lint]` 選 `E,F,I,B,UP`。`ruff` 以固定版本放進 `dev` extras（0.8.4），使本機與 CI 結果一致。既有違規只做必要修正：`F401`、`E731`、`I001` 自動修正，`E501` 手動換行，`B008` 若是 FastAPI 的慣用寫法則以設定允許。修正後新增一個測試執行 `ruff check` 並要求乾淨（沒有安裝 ruff 時 skip 並說明原因），使 lint 在預設測試中也受保護。
*替代方案*：`ruff format --check`——使用者已決定不加，因為會重新排版 41 個檔案。

### D6：CI 內安裝並驗證清單工具
下載 `kubectl` v1.31.4、`kustomize` v5.5.0 與 `kubeconform` v0.6.7 的官方 linux/amd64 發行檔，與官方公布的 SHA-256 比對，失敗即中止；安裝後執行 `kubectl version --client`、`kustomize version` 與 `kubeconform -v`，缺任何一個就在跑測試前失敗。kubectl 也要固定：runner 預裝的是較新版本（首次真實執行時為 v1.37.2），而 `docs/kubernetes.md` 記錄的版本由既有測試比對，不能放寬。`kubeconform` 需要下載 Kubernetes schema，runner 有網路，沒有問題；快取 schema 目錄可加速但不是必要。

### D7：api 映像每次建置、不推送
`build-api` 用 `docker/build-push-action` 以 `push: false` 建置 `deploy/docker/api.Dockerfile`，並啟用 GitHub Actions 的建置快取（api 很小，不會超過快取上限）。建置後執行容器的 `python -c "import app.run"` 做冒煙檢查，確認映像內的相依套件完整。

### D8：發佈只在 main，worker 先清磁碟、不使用建置快取
`publish` 工作 `if: github.event_name == 'push' && github.ref == 'refs/heads/main'`。步驟：刪除 runner 上不需要的大型預裝套件（dotnet、Android SDK、ghc、CodeQL）以騰出磁碟 → 登入 GHCR → 建置並推送 api 與 worker 兩個映像，標籤為 `ghcr.io/<owner 小寫>/openmot-api|openmot-worker:<sha>` 與 `:main`。worker 不使用 GHA 建置快取（約 11 GB 超過 10 GB 的快取上限，反而浪費時間）。
*假設*：使用者的決定是「worker 只在 main 建置並推送」；這裡也把 api 一起推送，因為 Kubernetes 部署需要兩個映像，而 api 的建置成本低。若只想推 worker，刪掉 api 的推送步驟即可。

### D9：第三方 action 與工具固定版本
`actions/checkout`、`actions/setup-python`、`docker/setup-buildx-action`、`docker/login-action`、`docker/build-push-action` 都固定到明確的版本（完整 SHA 或精確的版本標籤，不使用 `@main`、`@master` 或僅主版本的浮動標籤以外的分支名稱）。測試會檢查沒有以分支名稱引用的 action。

### D10：驗證分層
1. **本機靜態**：以 pyyaml 解析工作流程並檢查結構（觸發、權限、`needs`、發佈條件、並行設定、固定版本）；用 `actionlint` 檢查語法與表達式。
2. **本機重現**：在乾淨的虛擬環境用與 CI 相同的指令安裝 CPU 版 PyTorch、執行 lint 與預設測試；用 `docker build` 建置 api 映像。
3. **真實的 GitHub 執行**：推送一個分支，用 `gh run watch` 觀察 lint、測試與 api 建置的結果，並刻意推送一個含 lint 違規的分支確認會失敗。這需要 `gh` 已登入；未登入時由使用者執行並貼回結果。
4. **main 發佈**：只有合併或推送到 `main` 才會觸發，會建立 GHCR 套件並推送約 11 GB，是對外且難以還原的動作，**實作到這一步要先取得使用者同意**。

## Risks / Trade-offs

- [worker 映像接近 runner 磁碟上限且耗時] → D8 的清理步驟；仍失敗時，記錄實際的磁碟與耗時數字，再決定縮減層或改用自架 runner。
- [CPU 版 PyTorch 與 CUDA 版的行為差異] → CI 只保證不需要 GPU 的行為；GPU 測試維持手動執行，文件註明。
- [`ruff` 新版本新增規則使 CI 突然失敗] → 固定 ruff 版本。
- [發佈會在使用者帳號建立私有套件] → 實作時取得同意；預設可見性為私有。
- [第一次 `kubeconform` 下載 schema 較慢] → 可接受；失敗時錯誤訊息已區分下載失敗。
- [CI 無法在本機完整執行] → D10 的分層，且把可在本機重現的部分都重現。

## Migration Plan

沒有既有的 CI 需要遷移。新增檔案與設定後，現有流程不受影響。回滾是刪除 `.github/workflows/ci.yml`；`pyproject.toml` 的 ruff 設定與 lint 修正無害，可保留。
