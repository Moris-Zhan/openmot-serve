# Proposal

## Why

README 的 Quick start 只寫了預設的啟動指令，會把 `models/yolox_x.pth` 以 bind mount 掛進 worker。當 Docker daemon 看不到主機路徑時（例如在 k8s pod 內、`/mnt` 是 NFS），掛進去的會是空目錄，worker 因 `Is a directory` 不斷重啟。2026-11-24 在這台機器上照 Quick start 啟動時實際遇到，改疊加 `docker-compose.weights-volume.yml` 後才正常。`docs/service.md` 的「權重放置」已寫明這個情況，但讀者不會在 README 看到提示。

## What Changes

- README 的 Quick start 增加一小段英文提示：看到 worker 反覆重啟且日誌出現 `Is a directory` 時，表示 daemon 看不到主機路徑，改疊加 `docker-compose.weights-volume.yml`，細節連回 `docs/service.md`，不複製建立卷的步驟。
- 中文版 `README-ZH.md`（2026-11-24 已依 `README.md` 翻譯建立）的「快速上手」同步加上同一則提示；`README.md` 開頭加一行連到中文版。
- `tests/test_readme_quickstart.py` 增加測試：Quick start 提到該疊加檔與 `Is a directory` 症狀，所列檔案存在，章節仍在 30 行以內；中文版的快速上手也要有同一個疊加檔與同樣的啟動指令，兩份不會悄悄分歧。
- 不改程式、不改 compose、不改 `docs/service.md`。

## Capabilities

### New Capabilities

無。

### Modified Capabilities

無。純文件變更，沒有規格層級的行為改變，`.openspec.yaml` 設 `skip_specs: true`。

## Impact

- 影響檔案：`README.md`、`README-ZH.md`、`tests/test_readme_quickstart.py`。
- 沒有 API、相依套件或執行行為的變更。
- 已驗證的事實：在 daemon 看不到路徑的環境，疊加 `docker-compose.weights-volume.yml` 後 api、worker、redis 皆 healthy，並成功完成 MOT17-09 前 60 張影格的追蹤。
