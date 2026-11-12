# CI 指南

本文說明 `.github/workflows/ci.yml` 的行為，以及如何在本機重現每一項檢查。

**驗證狀態**：`lint`、`test`、`build-api` 已在 GitHub 託管 runner 上實際執行並通過；故意加入 lint 違規的分支會讓 `lint` 失敗且 `publish` 不執行。`publish` 已在 `main` 上實際執行並成功（run 43142361313，總耗時 5 分 50 秒），日誌顯示 api 與 worker 映像都以 commit SHA 與 `main` 標籤推送了 manifest；尚未從 GHCR 實際拉取映像驗證。

## 工作流程

每次 push（任何分支）與 pull request 都會觸發。同一個 ref 上較新的推送會取消進行中的舊執行，但 `main` 上不取消，避免發佈到一半被中止。不使用 `pull_request_target`。

| 工作 | 觸發 | 做什麼 |
|---|---|---|
| `lint` | 每次 push 與 pull request | 以固定版本的 ruff 執行 `ruff check .`，不做格式檢查 |
| `test` | 每次 push 與 pull request | 安裝 CPU 版 PyTorch、套件與固定版本的 kubectl、kustomize、kubeconform，執行預設測試 |
| `build-api` | 每次 push 與 pull request | 建置 api 映像（不推送），並以容器匯入 `app.run` 做冒煙檢查 |
| `publish` | 只有 push 到 `main`，且前三項都通過 | 建置並推送 api 與 worker 映像到 GHCR |

`test` 使用 Python 3.11，與 Dockerfile 一致。kubectl、kustomize 與 kubeconform 的版本記錄在 [`docs/kubernetes.md`](kubernetes.md)，CI 下載官方發行檔並比對 SHA-256，校驗失敗或缺任何工具會在跑測試前就失敗，所以 Kubernetes 清單的測試不會被靜默略過。

## 在本機重現

下列指令與工作流程相同。使用乾淨的虛擬環境最接近 CI：

```bash
# lint
pip install ruff==0.8.4
ruff check .

# test：先裝 CPU 版 PyTorch，再裝套件
pip install torch==2.7.1 torchvision==0.22.1 --index-url https://download.pytorch.org/whl/cpu
pip install -e ".[dev,service]"
python -m pytest

# build-api
docker build -f deploy/docker/api.Dockerfile -t openmot-api:ci .
docker run --rm openmot-api:ci python -c "import app.run"
```

在一個乾淨的 Python 3.11 虛擬環境用上述指令，加上 kubectl、kustomize、kubeconform，預設測試全部通過，沒有被略過的項目（約 2 分鐘，大部分是下載相依套件）。沒有安裝這三個工具時，Kubernetes 清單的測試會被略過，這是本機的刻意設計，CI 則一定會執行。

## 發佈的映像

`publish` 把映像推送到 GitHub Container Registry，名稱中的 `<owner>` 是小寫的倉庫擁有者：

| 映像 | 標籤 |
|---|---|
| `ghcr.io/<owner>/openmot-api` | commit SHA 與 `main` |
| `ghcr.io/<owner>/openmot-worker` | commit SHA 與 `main` |

取得方式（套件預設為私有，需要具有 `read:packages` 的 token）：

```bash
echo "$GHCR_TOKEN" | docker login ghcr.io -u <使用者名稱> --password-stdin
docker pull ghcr.io/<owner>/openmot-api:main
```

worker 映像約 11 GB。為了在託管 runner 上有足夠的磁碟，`publish` 會先刪除用不到的預裝套件，worker 不使用建置快取。第一次在 `main` 上的實際數字：清理磁碟後約 104 GB 可用；api 映像約 13 秒；worker 映像建置與推送約 3 分 53 秒（不使用快取），沒有遇到磁碟或逾時問題。

## 權限

工作流程預設只有 `contents: read`。只有 `publish` 另外有 `packages: write`，使用內建的 `GITHUB_TOKEN` 登入，沒有其他機密。第三方 action 都固定在明確的版本。pull request 與其他分支的執行沒有寫入套件的權限。

## 常見失敗

| 現象 | 原因與對策 |
|---|---|
| `lint` 失敗並列出規則代號 | 在本機執行 `ruff check .`；`F401`、`I001` 等可用 `ruff check --fix .` 修正，修完要看差異 |
| `test` 的 `sha256sum` 失敗 | 下載的工具與官方校驗碼不符；確認版本與校驗碼後再更新 |
| `test` 裡有一項比對工具版本的測試失敗 | runner 預裝的版本與文件不同。第一次真實執行時，runner 預裝 kubectl v1.37.2，而文件記錄 v1.31.4，所以 CI 改為安裝固定版本；升級工具時，要同時改工作流程、校驗碼與 `docs/kubernetes.md` |
| `test` 第一次執行較慢 | kubeconform 要下載 Kubernetes schema，下載失敗時測試會註明原因 |
| `build-api` 的冒煙檢查失敗 | 映像缺相依套件；先檢查 `deploy/docker/api-requirements.txt` |
| `publish` 磁碟不足或逾時 | worker 映像接近 runner 磁碟上限；記錄實際數字後，再決定縮減映像層或改用自架 runner |

## GPU 測試

標記為 `gpu`、`data`、`integration` 的測試不在 CI 內執行：CI 的 runner 沒有 GPU，也沒有 MOT17 資料集、Redis 或 Kubernetes 叢集。預設的 `python -m pytest` 已排除它們。要在有 GPU 的機器上執行：

```bash
python -m pytest -m gpu
```
