# Proposal

## Why

api 的對外埠目前以 `"${OPENMOT_API_PORT:-8000}:8000"` 發佈，沒有指定位址，Docker 會把它綁在主機所有網路介面上。api 沒有驗證、沒有 TLS，所以區域網路內任何機器都能提交工作、讀取結果。使用情境確定是只有擁有者自己透過 SSH 通道使用，沒有理由讓它對外開放。S3 與監控的 compose 早就綁在 `127.0.0.1`，api 是唯一的例外。

## What Changes

- `deploy/docker/docker-compose.yml` 的 api 埠映射加上 `127.0.0.1:` 前綴，只在主機的 loopback 介面上接受連線。
- `OPENMOT_API_PORT` 仍可改主機埠，預設仍是 8000。
- `docs/service.md` 補上「只綁 loopback，從別的機器請用 SSH 通道」的說明與指令範例。
- 測試：compose 設定測試新增斷言，api 的發佈位址必須是 `127.0.0.1`。
- 不改 api 程式碼、不加驗證與 TLS、不動 worker 與 Redis、不動 Kubernetes 清單、不動 S3 與監控的 compose。

## Capabilities

### New Capabilities

（無）

### Modified Capabilities

- `container-deployment`: 新增一條需求，規定 compose 只把 api 發佈在主機 loopback，而不是所有介面。

## Impact

- 受影響：`deploy/docker/docker-compose.yml`、`docs/service.md`、`tests/test_compose_file.py`。
- **BREAKING**：原本從區域網路其他機器直接連 `http://<這台機器>:8000` 的用法會失效，必須改用 SSH 通道。擁有者已確認只用 SSH 通道，所以沒有已知的使用者受影響。
- 本機測試（`tests/test_compose_e2e.py` 等）都連 `127.0.0.1`，不受影響。
- 容器內部的健康檢查與服務之間的連線走 compose 網路，不受影響。
