# Tasks

## 1. 以測試守住 loopback 綁定

- [x] 1.1 在 `tests/test_compose_file.py` 新增失敗測試：解析 compose 設定，api 每個發佈埠的 `host_ip` 為 `127.0.0.1`、容器埠為 8000；另一個測試在 `OPENMOT_API_PORT=18000` 下發佈埠為 18000 且位址不變。驗證：改檔案前執行 `pytest tests/test_compose_file.py`，這兩個測試失敗。
- [x] 1.2 擴充既有的 `test_only_the_api_publishes_a_port` 或新增並列測試，確認 redis 與 worker 沒有 `ports`。驗證：該測試通過。

## 2. 修改 compose

- [x] 2.1 把 `deploy/docker/docker-compose.yml` 的 api 映射改為 `"127.0.0.1:${OPENMOT_API_PORT:-8000}:8000"`。驗證：任務 1.1 的測試轉為通過，`docker compose -f deploy/docker/docker-compose.yml config` 顯示 `host_ip: 127.0.0.1`。
- [x] 2.2 確認 tensorrt、s3、monitoring、weights-volume 這些 compose 疊加後，api 的發佈位址仍是 `127.0.0.1`。驗證：對每個疊加組合執行 `docker compose -f … config --format json`，檢查 api 的 `host_ip`；若沒有這類測試，補一個參數化測試。

## 3. 文件

- [x] 3.1 在 `docs/service.md` 的 `OPENMOT_API_PORT` 說明處，寫明只綁 loopback，並加入 `ssh -N -L 8000:127.0.0.1:<埠> <使用者>@<主機>` 的通道範例。驗證：文件中可搜尋到 `127.0.0.1` 與 `ssh -N -L`；若專案已有檢查文件的測試，讓它涵蓋這一句。
- [x] 3.2 確認 README 沒有仍寫「從區域網路直連」的說明。驗證：搜尋 README 與 `docs/` 中的 `:8000` 與直連說明，逐一檢視。

## 4. 整體驗證

- [x] 4.1 執行 `pytest`、`ruff check`、`ruff format --check`，以及 `openspec validate bind-api-port-to-loopback --strict`。驗證：全部通過，並記錄 pytest 的通過與略過數。
- [x] 4.2 實機確認：啟動 compose 後，在這台機器上 `GET http://127.0.0.1:<埠>/health` 回 200，並用 `ss -ltnp` 確認監聽位址是 `127.0.0.1` 而非 `0.0.0.0`。驗證：貼出輸出；若沒有第二台機器可以測「從別台連線被拒絕」，在回報中明說這一項未驗證。啟動會用到 GPU，須先確認不干擾使用者的微調訓練。
