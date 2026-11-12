# Proposal

## Why

專案已有 569 項預設測試與兩個 Dockerfile，但沒有任何自動化：沒有 `.github/`、沒有 lint 設定，push 之後是否還能建置映像、測試是否通過，全靠開發者手動跑。路線圖 v0.4 的第一項就是「GitHub Actions：每次 push 做 lint、測試與映像建置」，也是 v0.4 可觀測性與日後任何部署的前提：沒有可信的 CI，後面疊上去的 metrics 與 dashboard 都沒有回歸保護。

## What Changes

- 新增 GitHub Actions 工作流程 `.github/workflows/ci.yml`，在每次 push 與 pull request 執行：
  - **lint**：`ruff check`（規則集 E、F、I、B、UP，行寬 120，排除 `eval/vendor`）。
  - **test**：在沒有 GPU 的託管 runner 上執行預設測試（不含 `gpu`、`data`、`integration`），使用 CPU 版 PyTorch；並安裝固定版本的 `kustomize` 與 `kubeconform`，使 Kubernetes 清單測試真的執行而不是 skip。
  - **build api**：建置 api 映像，驗證 Dockerfile 在乾淨環境下可建置，不推送。
- 在 push 到 `main` 時另外建置 worker 映像（先清理 runner 磁碟）並與 api 映像一起推送到 GHCR，標籤為 commit SHA 與 `main`。pull request 與其他分支不推送。
- 新增 `ruff` 設定與 dev 依賴，並修正既有程式碼中少量的違規（只修必要處，不重新排版）。
- 新增 CI 本身的測試（工作流程的結構與權限）與 lint 乾淨的測試。
- 新增文件 `docs/ci.md`：工作流程說明、在本機重現 CI、映像名稱與取得方式、讀失敗訊息。更新 README 的路線圖與狀態。
- 不包含：部署到叢集的 CD（沒有叢集，見 `verify-kubernetes-on-cluster`）、`ruff format --check`（41 個檔案需重新排版，會產生大量無關變更）、mypy、多版本 Python 矩陣、GPU runner、覆蓋率報告、版本發佈與標籤、Dependabot、可觀測性（另開 `add-observability`）。

## Capabilities

### New Capabilities
- `continuous-integration`: 每次 push 與 pull request 自動執行 lint、不需 GPU 的預設測試與映像建置驗證；main 上發佈映像；工作流程權限最小化、依賴固定版本。

### Modified Capabilities

（無。服務、容器與 Kubernetes 的行為都不變。）

## Impact

- **新增檔案**：`.github/workflows/ci.yml`、`docs/ci.md`、CI 與 lint 的測試。
- **修改檔案**：`pyproject.toml`（ruff 設定與 dev 依賴）、少量既有程式碼的 lint 修正（預期 `app/`、`worker/`、`jobs/`、`tracking/`、`tests/` 內數行，行為不變）、`README.md`。
- **外部系統**：GitHub Actions（託管 runner）、GHCR（`ghcr.io/moris-zhan/…`）。第一次在 main 上發佈會在使用者的 GitHub 帳號建立套件並推送約 11 GB 的映像，這是對外的動作，實作到該步驟時要先取得使用者同意。
- **風險**：worker 映像約 11.4 GB，接近託管 runner 的磁碟上限，需要清理並可能耗時較長；CPU 版 PyTorch 與 CUDA 版不同，只能驗證不需 GPU 的行為（GPU 測試仍需在有 GPU 的機器手動執行）。
