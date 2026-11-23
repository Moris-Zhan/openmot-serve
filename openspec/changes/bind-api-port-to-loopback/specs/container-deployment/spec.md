## ADDED Requirements

### Requirement: The api port is published on the host loopback only
系統 SHALL 只把 api 的對外埠發佈在主機的 loopback 位址（`127.0.0.1`），SHALL NOT 發佈在所有介面或其他位址。主機埠 SHALL 仍可用 `OPENMOT_API_PORT` 調整，預設 8000。Redis 與 worker SHALL 維持不發佈任何埠。

#### Scenario: The published address is loopback
- **WHEN** 解析 compose 設定，檢視 api 服務的發佈埠
- **THEN** 每一個發佈埠的主機位址都是 `127.0.0.1`，容器埠是 8000

#### Scenario: The host port can still be changed
- **WHEN** 以 `OPENMOT_API_PORT=18000` 解析 compose 設定
- **THEN** api 的發佈埠為 `127.0.0.1:18000`，容器埠仍是 8000

#### Scenario: Other machines cannot reach the api directly
- **WHEN** 服務啟動後，區域網路內的另一台機器連線到這台機器的 api 埠
- **THEN** 連線被拒絕，而在這台機器上連 `127.0.0.1` 的 api 埠回應 `GET /health` 為 `200`

#### Scenario: Redis and the worker publish nothing
- **WHEN** 解析 compose 設定
- **THEN** redis 與 worker 都沒有發佈埠
