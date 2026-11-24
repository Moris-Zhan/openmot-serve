# Tasks

## 1. 先寫會失敗的測試

- [x] 1.1 在 `tests/test_readme_quickstart.py` 新增測試：Quick start 提到 `docker-compose.weights-volume.yml` 與 `Is a directory`，且啟動指令範例含疊加檔（測試目前應失敗）
- [x] 1.2 確認既有測試仍涵蓋：所列檔案存在（`deploy/docker/docker-compose.weights-volume.yml` 會被納入檢查）、章節不超過 30 行、連回 `docs/service.md`
- [x] 1.3 新增測試（只比對 Quick start 一節，不比對整份文件的章節數或數字）：`README-ZH.md` 的「快速上手」含同樣的 `docker compose ... up -d --build` 指令、`docker-compose.weights-volume.yml` 與 `Is a directory`，所列檔案存在，連回 `docs/service.md`；`README.md` 開頭連到 `README-ZH.md`（測試目前應失敗）

## 2. 修改 README

- [x] 2.1 在 README Quick start 補一小段英文提示：daemon 看不到主機路徑時（k8s pod、NFS）worker 會因 `Is a directory` 重啟，改疊加 `docker-compose.weights-volume.yml`，提示放在啟動指令區塊之後，附一行可直接複製的 `docker compose -f deploy/docker/docker-compose.yml -f deploy/docker/docker-compose.weights-volume.yml up -d --build`；預設指令不變；建立卷的步驟見 `docs/service.md` 的「權重放置」，不複製；Quick start 仍在 30 行內
- [x] 2.2 在 `README-ZH.md` 的「快速上手」補上同一則提示（繁體中文，指令與檔名不翻譯），並在 `README.md` 標題下加一行 `English | [繁體中文](README-ZH.md)`
- [x] 2.3 跑 `tests/test_readme_quickstart.py` 確認通過，並證明移除新段落後測試會失敗

## 3. 驗證

- [x] 3.1 跑完整 pytest、`ruff check`、`ruff format --check` 與 `openspec validate --changes --strict`，如實記錄結果
