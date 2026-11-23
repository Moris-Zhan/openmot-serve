# Tasks

## 1. 以測試定義快速上手一節

- [x] 1.1 新增 `tests/test_readme_quickstart.py`，檢查 README 在 `## API` 之前有 `## Quick start`，且該節：包含 `docker compose -f deploy/docker/docker-compose.yml up`、`/health`、`127.0.0.1` 與 `ssh -N -L`；每個提到的 `deploy/docker/*.yml` 與 `docs/` 連結目標檔案都存在；長度不超過 30 行（避免複製 `docs/service.md`）。驗證：改 README 前執行 `pytest tests/test_readme_quickstart.py`，測試失敗。

## 2. 寫快速上手一節

- [x] 2.1 在 `README.md` 的 `## API` 之前加入英文的 `## Quick start`：前置條件一行（NVIDIA GPU、Compose v2、`models/yolox_x.pth`）、啟動與 `ps` 指令、`/health` 驗證與預期輸出、SSH 通道指令並註明 api 只綁在 `127.0.0.1`、連到 `docs/service.md` 的容器部署與疑難排解。驗證：任務 1.1 的測試轉為通過；既有的 `tests/test_readme_api.py` 等 README 測試仍通過。
- [x] 2.2 對照 `docs/service.md` 的容器部署一節，逐條確認 README 的指令與預期輸出與它一致，不另外發明。驗證：兩處的指令字串相同，並在回報中列出對照結果。

## 3. 整體驗證

- [x] 3.1 執行 `pytest`、`ruff check`、`ruff format --check`，以及 `openspec validate add-readme-quickstart --strict`。驗證：全部通過，並記錄 pytest 的通過與略過數。

## Workflow follow-up

- 本變更要在 `bind-api-port-to-loopback` 合併之後才能合併，否則 README 會描述尚未生效的 loopback 行為。
- 合併後以 `/opsx:archive` 封存。因為沒有 delta spec，不需要同步主規格。
