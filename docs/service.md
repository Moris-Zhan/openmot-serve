# 非同步追蹤服務（Service）

這個服務讓你用 HTTP 提交一段影格序列，不必等追蹤完成，之後再查詢進度、取回 MOTChallenge 格式的結果。流程是：

1. `POST /jobs` 上傳影格 zip，立刻拿到 job ID（Job 進入 `queued`）。
2. worker 取出 Job 執行追蹤（`running`），結束後變成 `succeeded` 或 `failed`。
3. `GET /jobs/{job_id}` 查狀態，成功後 `GET /jobs/{job_id}/result` 取結果。

> 本文涵蓋 HTTP API、上傳規則、本機端對端驗證與容器部署。

## 在本機試用 API（假後端）

不需要 GPU、Redis 或 Docker。`scripts/fake_service.py` 用記憶體內的 Job 儲存，並讓每個 Job 立刻「追蹤完成」（每格一個框），用來熟悉 API：

```shell
mkdir -p /tmp/openmot_demo/storage
OPENMOT_STORAGE_DIR=/tmp/openmot_demo/storage python scripts/fake_service.py 8000
```

服務預設監聽 `127.0.0.1:8000`。以下範例都假設它正在執行。

## 把影格打包成 zip

zip 內的影像必須直接放在最上層（不可有子資料夾），檔名為連續編號。先產生 10 張合成影格再打包：

```bash
python -c "
import cv2, numpy as np
from pathlib import Path
folder = Path('/tmp/openmot_demo/frames')
folder.mkdir(parents=True, exist_ok=True)
for number in range(1, 11):
    cv2.imwrite(str(folder / f'{number:06d}.jpg'), np.full((540, 960, 3), 128, np.uint8))
"
cd /tmp/openmot_demo/frames && python -m zipfile -c ../frames.zip *.jpg
```

用自己的資料時，在影格資料夾內執行 `python -m zipfile -c ../frames.zip *.jpg` 即可；不要打包資料夾本身，也不要用 macOS 的「壓縮」功能（它會加入 `__MACOSX/`）。

## 提交 Job：`POST /jobs`

以 multipart 上傳，欄位名稱為 `file`：

```bash
curl -s -X POST -F "file=@/tmp/openmot_demo/frames.zip" http://127.0.0.1:8000/jobs
```

成功回應 `202 Accepted`，Job 此時已持久化並排入佇列：

```json
{"job_id": "3f2a9c1e7b6d4a58b0c1d2e3f4a5b6c7", "status": "queued"}
```

每次提交都會得到新的 job ID，即使內容相同。後續範例用 shell 變數保存它：

```bash
JOB_ID=$(curl -s -X POST -F "file=@/tmp/openmot_demo/frames.zip" http://127.0.0.1:8000/jobs | python -c "import json,sys; print(json.load(sys.stdin)['job_id'])")
echo "$JOB_ID"
```

## 查詢狀態：`GET /jobs/{job_id}`

```bash
curl -s http://127.0.0.1:8000/jobs/$JOB_ID
```

```json
{"job_id": "3f2a9c1e7b6d4a58b0c1d2e3f4a5b6c7", "status": "succeeded", "reason": null, "created_at": 1760000000.1, "updated_at": 1760000000.4}
```

`status` 只會是以下四種，並且只往前走，不會倒退：

| 狀態 | 意義 |
| --- | --- |
| `queued` | 已接受，等待 worker |
| `running` | worker 正在追蹤 |
| `succeeded` | 完成，可以取結果 |
| `failed` | 失敗，`reason` 說明原因 |

時間欄位是 Unix 秒數。未知的 job ID 回 `404`。

## 取回結果：`GET /jobs/{job_id}/result`

```bash
curl -s http://127.0.0.1:8000/jobs/$JOB_ID/result
```

成功時回 `200`，內容是 `text/plain` 的 MOTChallenge 格式，每行 10 個欄位（`frame,id,left,top,width,height,confidence,-1,-1,-1`），與命令列 `python -m tracking.run` 的輸出一致（以下是假後端對 10 張影格回傳的前兩行）：

```text
1,1,100.00,100.00,50.00,100.00,0.9000,-1,-1,-1
2,1,100.00,100.00,50.00,100.00,0.9000,-1,-1,-1
```

Job 還沒成功時不會回傳任何部分內容，狀態碼可以區分情況：

| 情況 | 狀態碼 | 意義 |
| --- | --- | --- |
| `queued` / `running` | `409` | 還沒完成，稍後再試 |
| `failed` | `410` | 不會有結果了，訊息含失敗原因 |
| 未知 job ID | `404` | |

輪詢時建議看 `GET /jobs/{job_id}` 的 `status`，直到 `succeeded` 或 `failed` 再決定是否取結果。

## 健康檢查：`GET /health`

```bash
curl -s -w "\n%{http_code}\n" http://127.0.0.1:8000/health
```

```text
{"status":"ok"}
200
```

佇列後端可連線且共用儲存可寫入時回 `200`；否則回 `503`，並在 `problems` 指出哪一項不可用：

```json
{"status": "unavailable", "problems": ["queue backend is unavailable: ..."]}
```

## 上傳驗證規則

驗證在 API 收到上傳時同步完成，不合格的上傳不會建立 Job，也不會在儲存空間留下任何檔案。

### zip 內容

- 所有項目必須直接位於壓縮檔最上層（平鋪），不可有子資料夾。macOS 產生的 `__MACOSX/` 也算子資料夾，會被拒絕。
- 只接受影像：副檔名為 `.jpg`、`.jpeg`、`.png`（不分大小寫）。其他檔案（例如 `notes.txt`）一律拒絕，錯誤訊息會點名該檔案。
- 檔名必須是連續編號，例如 `000001.jpg`、`000002.jpg`……，不可跳號、不可含非數字檔名。
- 每張影格都必須能被解碼成影像。
- 不可含重複名稱的項目，也不可是空壓縮檔。
- 路徑不可含絕對路徑、`..` 或反斜線（防止 zip slip）。

### 大小上限

| 環境變數 | 預設 | 說明 |
| --- | --- | --- |
| `OPENMOT_MAX_UPLOAD_BYTES` | 1 GiB | 上傳檔本身的上限，串流時即時檢查，超過就中止 |
| `OPENMOT_MAX_UNPACKED_BYTES` | 4 GiB | 壓縮檔宣告的解壓後總大小上限，不可小於上傳上限 |

解壓前先從壓縮檔的目錄資訊加總宣告大小，超過上限就直接拒絕，不會寫出任何檔案，所以壓縮炸彈不會佔用磁碟。以上預設值是暫定值，會依 MOT17 實測調整。

### 錯誤碼

| 情況 | 狀態碼 |
| --- | --- |
| 上傳超過大小上限、宣告的解壓大小超過上限 | `413` |
| 其他不合格（非 zip、沒有影格、非影像、路徑不安全、編號不連續、影格損毀） | `422` |
| 缺少 `file` 欄位 | `422`（由框架回應） |
| 無法排入佇列 | `503`（影格已移除，該 Job 標為 `failed`） |
| 儲存空間寫入失敗 | `500` |

錯誤回應的內容是 `{"detail": "原因"}`，例如上傳一個不是 zip 的檔案：

```bash
echo "not a zip" > /tmp/openmot_demo/not_a_zip.txt
curl -s -w "\n%{http_code}\n" -X POST -F "file=@/tmp/openmot_demo/not_a_zip.txt" http://127.0.0.1:8000/jobs
```

```text
{"detail":"the upload is not a zip archive"}
422
```

### 已知限制

- 影格會在提交時整批解碼驗證，高解析度的長序列會讓 `POST /jobs` 多花數秒。
- 框架會先把整個 multipart 請求收完才進入處理，所以 `OPENMOT_MAX_UPLOAD_BYTES` 只能在收完後擋下過大的上傳。對外服務時，請在反向代理（例如 nginx 的 `client_max_body_size`）同樣限制請求大小。

## 測試用 Redis

整合測試（`integration` 標記，預設不執行）需要一個真的 Redis：

```shell
docker run -d --rm --name openmot-test-redis -p 127.0.0.1:16379:6379 redis:7-alpine
python -m pytest -m integration
docker stop openmot-test-redis
```

預設連線位址為 `redis://localhost:16379/0`，可用 `OPENMOT_TEST_REDIS_URL` 覆寫。沒有 Redis 時這些測試會自動跳過。

## 本機端對端驗證（不經容器）

驗證環境：RTX 5090、`redis:7-alpine`（Docker）、`python -m worker.run`、`python -m app.run`，權重為 `models/yolox_x.pth`，精度 fp16。

```shell
export OPENMOT_STORAGE_DIR=/path/to/storage OPENMOT_REDIS_URL=redis://localhost:16379/0
export OPENMOT_WEIGHTS=$PWD/models/yolox_x.pth
python -m worker.run   # 先通過環境檢查並載入偵測器，才開始消費佇列
python -m app.run      # 監聽 0.0.0.0:8000
```

### 正常流程

以 MOT17-09-FRCNN（525 張影格，zip 108 MB）提交：

| 項目 | 結果 |
| --- | --- |
| `POST /jobs`（上傳、解壓、解碼驗證、排入佇列） | 約 3.4 秒 |
| worker 處理（偵測加追蹤） | 約 11.7 秒 |
| 提交到 `succeeded` 的總時間 | 約 15.6 秒 |
| `python -m tracking.run` 同一序列 | 約 15.8 秒（含載入模型） |
| Result 與 CLI 輸出 | `cmp` 逐位元組相同（6125 列） |

### 中途終止 worker

以較短的逾時（`OPENMOT_LOCK_TIMEOUT_SECONDS=30`、`OPENMOT_TASK_TIME_LIMIT_SECONDS=25`）重新啟動 worker，Job 開始 `running` 後用 `kill -9` 終止 worker：

- worker 不在時，Job 會維持 `running`，因為沒有任何行程可以更新它。
- 重新啟動 worker 後，訊息被重新投遞，舊鎖過期後由新 worker 接手，Job 最後變成 `succeeded`，Result 與 CLI 輸出逐位元組相同。從終止到完成約 126 秒，其中包含 worker 重啟與載入模型（約 20 秒）和等待重新投遞。
- 重新投遞要等 `visibility_timeout`（等於鎖逾時），預設 1800 秒；上面的 126 秒是因為測試時縮短了逾時。

所以 Job 不會永久停在 `running`，前提是 worker 會被重新啟動（compose 的 `restart` 政策負責這件事）。

## 容器部署

用 Docker Compose 一個指令啟動 Redis、API 與 GPU worker。

### 前置條件

- 一張 NVIDIA GPU（驗證環境為 RTX 5090，CUDA 12.8 以上）與對應的驅動程式。
- Docker 與 Compose v2，並安裝 NVIDIA Container Toolkit。確認方式：`docker info --format '{{json .Runtimes}}'` 的輸出要含 `nvidia`。
- 權重檔 `models/yolox_x.pth`。權重不會進映像，由 compose 以唯讀方式掛進 worker，worker 載入時會驗證 SHA-256。

### 啟動

在 repo 根目錄執行：

```bash
docker compose -f deploy/docker/docker-compose.yml up -d --build
docker compose -f deploy/docker/docker-compose.yml ps
```

第一次建置 worker 映像需要數分鐘（含 cu128 版 torch，映像約 11 GB）。三個服務都顯示 `healthy` 後，API 會在 `http://127.0.0.1:8000`：

```bash
python -c "import httpx; print(httpx.get('http://127.0.0.1:8000/health').json())"
```

```text
{'status': 'ok'}
```

提交與查詢的方式與前面的 API 章節相同。停止服務用 `docker compose -f deploy/docker/docker-compose.yml down`；加上 `-v` 會連同 Job 資料一起刪除。

### 權重放置

預設掛載 `models/yolox_x.pth`。權重在別的位置時，用 `OPENMOT_WEIGHTS_FILE` 指定：

```shell
OPENMOT_WEIGHTS_FILE=/data/weights/yolox_x.pth docker compose -f deploy/docker/docker-compose.yml up -d
```

bind mount 要求 Docker daemon 看得到該路徑。如果 daemon 在另一個檔案系統命名空間（遠端 daemon、某些容器內的 Docker），掛進去的會是空目錄，worker 會因為 `Is a directory` 而啟動失敗。這時改把權重放進具名 volume，再疊加 `docker-compose.weights-volume.yml`：

```bash
docker volume create openmot-weights
docker create --name weights-loader -v openmot-weights:/models python:3.11-slim
docker cp models/yolox_x.pth weights-loader:/models/yolox_x.pth
docker rm weights-loader
docker compose -f deploy/docker/docker-compose.yml -f deploy/docker/docker-compose.weights-volume.yml up -d --build
```

### 環境變數

compose 已替 api 與 worker 設好儲存目錄、Redis 位址與權重路徑。其餘變數沿用預設值；要調整時，在 `docker-compose.yml` 或自己的 override 檔的 `environment` 區段加入。無效值會讓服務在啟動時失敗，並指出是哪個變數。

| 變數 | 預設值 | 使用者 | 說明 |
| --- | --- | --- | --- |
| `OPENMOT_STORAGE_DIR` | compose 設為 `/data/storage` | api、worker | 序列與結果的根目錄，必須是絕對路徑，必填 |
| `OPENMOT_REDIS_URL` | compose 設為 `redis://redis:6379/0` | api、worker | Job Status 與佇列所在的 Redis |
| `OPENMOT_WEIGHTS` | compose 設為 `/models/yolox_x.pth` | worker | 權重路徑，載入時驗證 SHA-256 |
| `OPENMOT_PRECISION` | `fp16` | worker | 推論精度，`fp16` 或 `fp32` |
| `OPENMOT_MAX_UPLOAD_BYTES` | 1 GiB | api | 上傳 zip 的大小上限 |
| `OPENMOT_MAX_UNPACKED_BYTES` | 4 GiB | api | 解壓後總量上限，不可小於上傳上限 |
| `OPENMOT_LOCK_TIMEOUT_SECONDS` | 1800 | worker | Job 鎖逾時，同時是訊息重新投遞的等待時間 |
| `OPENMOT_TASK_TIME_LIMIT_SECONDS` | 1500 | worker | 單一 Job 的時間上限，必須小於鎖逾時 |

Compose 層級的變數：`OPENMOT_API_PORT`（對外埠，預設 8000）、`OPENMOT_WEIGHTS_FILE`（主機上的權重路徑）。

### 卷與資料保存

| 卷 | 內容 | 重建容器後 |
| --- | --- | --- |
| `storage` | `jobs/<id>/frames/`、`result.txt`、`result.meta.json`，api 與 worker 共用 | 保留 |
| `redis-data` | Redis 的 AOF（Job Status 與佇列） | 保留 |

重建 api 或 worker（例如 `up -d --force-recreate`）不會遺失已完成 Job 的狀態與結果。序列與結果不會自動清理，需要時請自行刪除 `jobs/<id>/` 與對應的 Redis 鍵，磁碟用量要自行監控。

### 疑難排解

| 症狀 | 原因與處理 |
| --- | --- |
| `docker compose ps` 中 worker 一直是 `Restarting` | 看 `docker compose logs worker`。常見原因見下面三列 |
| 日誌出現 `CUDA is not available to PyTorch` | 容器看不到 GPU。確認 NVIDIA Container Toolkit 已安裝，且 `docker info` 的 Runtimes 含 `nvidia`。worker 不會退回 CPU，會持續重啟直到能看到 GPU |
| 日誌出現 `Is a directory: '/models/yolox_x.pth'` | daemon 看不到權重路徑，改用上面的具名 volume 做法 |
| 日誌出現 SHA-256 不符 | 權重檔不是預期的 YOLOX-X 官方檔案，重新下載 |
| `api` 是 `unhealthy`，或 `GET /health` 回 `503` | 回應的 `problems` 會指出是佇列後端還是儲存不可用 |
| Job 停在 `running` | worker 在處理途中被終止。重新啟動 worker 後，訊息在鎖逾時之後被重新投遞並由新 worker 接手；預設最長約 30 分鐘 |
| Job 停在 `queued` | worker 沒有在執行或不健康，啟動它之後會被處理 |
| worker 在處理長 Job 時暫時顯示 `unhealthy` | solo pool 忙碌時不回應 ping；健康檢查容忍約 5 分鐘，超過時請對照日誌確認它仍在處理 |
| 想在沒有 GPU 的機器上跑 | 不支援。可以用前面的假後端熟悉 API |

### 容器端對端測試

`integration` 標記的測試會真的起一組 compose、提交影格、重建容器並確認結果仍在。需要 Docker、nvidia runtime、權重與 MOT17 資料，缺任何一項會 skip 並說明原因：

```shell
python -m pytest -m integration tests/test_compose_e2e.py
```

daemon 看不到本機路徑時，用 `OPENMOT_E2E_COMPOSE_OVERRIDE=deploy/docker/docker-compose.weights-volume.yml` 指定上面的 volume 版 override。
