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
| `OPENMOT_PRECISION` | `fp16` | worker | 推論精度：`torch` 為 `fp16` 或 `fp32`，`onnx` 為 `fp32`，`tensorrt` 為 `fp16` 或 `int8`；TensorRT 的 compose override 以同一個變數指定 |
| `OPENMOT_BACKEND` | `torch` | worker | 偵測後端：`torch`、`onnx` 或 `tensorrt`；與 `OPENMOT_PRECISION` 的組合要合法（`onnx` 只支援 `fp32`，`tensorrt` 支援 `fp16` 或 `int8`），不會由後端推斷精度 |
| `OPENMOT_ENGINE` | 未設定 | worker | `onnx` 或 `tensorrt` 後端使用的 ONNX／engine 檔的絕對路徑；和 `torch` 後端同時設定會啟動失敗 |
| `OPENMOT_MAX_UPLOAD_BYTES` | 1 GiB | api | 上傳 zip 的大小上限 |
| `OPENMOT_MAX_UNPACKED_BYTES` | 4 GiB | api | 解壓後總量上限，不可小於上傳上限 |
| `OPENMOT_LOCK_TIMEOUT_SECONDS` | 1800 | worker | Job 鎖逾時，同時是訊息重新投遞的等待時間 |
| `OPENMOT_TASK_TIME_LIMIT_SECONDS` | 1500 | worker | 單一 Job 的時間上限，必須小於鎖逾時 |

Compose 層級的變數：`OPENMOT_API_PORT`（對外埠，預設 8000）、`OPENMOT_WEIGHTS_FILE`（主機上的權重路徑）。

### TensorRT worker

預設的 worker 以 PyTorch 偵測。要改用 TensorRT engine，使用 `docker-compose.tensorrt.yml` 與映像的 `tensorrt` build target（多裝固定版本的 `onnx`、`onnxruntime-gpu`、`tensorrt-cu12` 與匯出、建置 engine 的指令）。ONNX 與 engine 不會進映像，放在具名卷 `openmot-engines`，由 worker 以唯讀掛載。

1. 建立兩個卷（權重卷的建立方式見上面的「權重放置」）：

   ```bash
   docker volume create openmot-weights   # 若已建立並放入 yolox_x.pth 就略過
   docker volume create openmot-engines
   ```

2. 用同一個映像的 `engine-builder` 匯出 ONNX 並建立 engine，TensorRT 版本與之後執行時一致。`docker-compose.tensorrt.yml` 在載入時就要求 `OPENMOT_ENGINE_FILE`，所以建置階段先給任意占位值：

   ```bash
   export STACK="-f deploy/docker/docker-compose.yml -f deploy/docker/docker-compose.weights-volume.yml -f deploy/docker/docker-compose.tensorrt.yml"
   export OPENMOT_ENGINE_FILE=pending
   docker compose $STACK --profile tools run --rm engine-builder -m inference.export_onnx --weights /models/yolox_x.pth --output-dir /models/engines
   docker compose $STACK --profile tools run --rm engine-builder -m inference.build_engine --weights /models/yolox_x.pth --output-dir /models/engines
   docker compose $STACK --profile tools run --rm --entrypoint ls engine-builder /models/engines
   ```

   最後一行列出卷內的檔案；`*.engine` 的檔名含權重雜湊、輸入尺寸、TensorRT 版本、GPU 型號與精度，例如 `yolox_x-5652330b-800x1440-trt10.13.3.9-nvidia-geforce-rtx-5090-fp16.engine`。

3. 以該檔名設定 `OPENMOT_ENGINE_FILE` 並啟動：

   ```bash
   export OPENMOT_ENGINE_FILE=<上一步列出的 .engine 檔名>
   docker compose $STACK up -d --build
   ```

   沒有設定 `OPENMOT_ENGINE_FILE` 時，compose 會以訊息 `set OPENMOT_ENGINE_FILE to the engine file name inside the engines volume` 拒絕載入。要使用不同名稱的卷，設定 `OPENMOT_ENGINES_VOLUME`。同一個檢查也適用於 `logs`、`down` 等其他 compose 指令，所以上面用 `export` 讓整個 shell 都有這個變數。

4. 確認：worker 載入成功後會在標準輸出印一行偵測器設定，只有後端、精度與（有產物時的）路徑，不含檔案內容：

   ```text
   detector: backend=tensorrt precision=fp16 engine=/models/engines/<engine 檔名>
   ```

   用 `docker compose $STACK logs worker` 查看；預設的 `torch` 後端印 `detector: backend=torch precision=fp16`，沒有路徑。Job 的 `result.meta.json` 在非 `torch` 後端時多一個 `"backend": "tensorrt"`，`torch` 的內容與以前逐位元組相同。

**以 INT8 engine 服務。** INT8 engine 要先依 [`docs/evaluation.md`](evaluation.md) 的「INT8 量化」用 `python -m inference.build_engine --precision int8` 建好；它需要 MOT17 的校準影格，所以在有資料的環境建，不在映像內建。把 engine 與同名的 `.json` sidecar 放進 `openmot-engines` 卷，再以環境變數指定精度與檔名：

```bash
export OPENMOT_PRECISION=int8
export OPENMOT_ENGINE_FILE=<INT8 engine 檔名，以 -int8.engine 結尾>
docker compose $STACK up -d --build
```

- 未設定 `OPENMOT_PRECISION` 時仍是 `fp16`，渲染出的設定與以前相同。
- 精度必須與 engine 的 sidecar 一致：未指定精度卻掛載 INT8 engine（或相反）時，worker 在處理任何 Job 之前就啟動失敗，訊息指名精度不符。
- 映像要用目前的程式碼重建，所以用 `up -d --build`：在 INT8 支援加入之前建的 `openmot-worker:tensorrt` 映像裡，`tracking.run` 只接受 `fp16` 與 `fp32`，worker 也會拒絕 `int8`。
- 這只說明 INT8 engine 可以被服務，不是建議：它的合併 HOTA 比 PyTorch FP16 低 1.51，標為「不建議」，見 `docs/evaluation.md`。端對端驗證的結果見下面的「驗證」。

何時要重建 engine：權重檔、TensorRT 版本（升級映像）或 GPU 型號改變時。worker 載入時會比對 engine 旁邊的 sidecar，不符就啟動失敗並指名不符的項目與重建指令，不會處理任何 Job。

限制（如實說明）：

- engine 綁定 GPU 型號與 TensorRT 版本，換機器或升級映像後要重建。
- `onnx` 後端只用於驗證與比較，比 PyTorch 慢（實測約 21.8 FPS 對 55 FPS），不是部署選項。
- Kubernetes 沒有 TensorRT 映像：k8s 的 ConfigMap 固定 `OPENMOT_BACKEND=torch`，TensorRT 目前只在 compose 提供。k8s 的 api 與 worker 共用同一份 ConfigMap，所以那裡的錯誤後端設定會讓兩者都啟動失敗；compose 的環境變數是逐服務設定，`OPENMOT_BACKEND` 只加在 worker。
- CI 不建置 `tensorrt` target（託管 runner 沒有 GPU，建置要下載數 GB 的套件且無法執行），CI 只跑 Dockerfile 與 compose 的結構測試；建置與端對端只在有 GPU 的機器驗證。
- 在預設映像設 `OPENMOT_BACKEND=tensorrt` 會在啟動時失敗並指出缺少 `tensorrt-cu12` 與安裝方式，不是在設定階段被擋下。

### 卷與資料保存

| 卷 | 內容 | 重建容器後 |
| --- | --- | --- |
| `storage` | `jobs/<id>/frames/`、`result.txt`、`result.meta.json`，api 與 worker 共用 | 保留 |
| `redis-data` | Redis 的 AOF（Job Status 與佇列） | 保留 |

重建 api 或 worker（例如 `up -d --force-recreate`）不會遺失已完成 Job 的狀態與結果。序列與結果不會自動清理，需要時請自行刪除 `jobs/<id>/` 與對應的 Redis 鍵，磁碟用量要自行監控。

### S3 物件儲存

預設仍是檔案系統（`storage` 卷），不設定任何東西時行為不變。要改用 S3 相容的物件儲存，設定 `OPENMOT_STORAGE_BACKEND=s3`：api 把影格上傳到 bucket，worker 下載到本機暫存、追蹤後把 `result.meta.json` 與 `result.txt` 上傳回 bucket（先上傳完才標為 `succeeded`），api 再從 bucket 讀取結果。物件鍵與檔案系統的版面相同：`jobs/<id>/frames/<名稱>`、`jobs/<id>/result.txt`、`jobs/<id>/result.meta.json`。

| 變數 | 預設值 | 使用者 | 說明 |
| --- | --- | --- | --- |
| `OPENMOT_STORAGE_BACKEND` | `filesystem` | api、worker | `filesystem` 或 `s3` |
| `OPENMOT_S3_BUCKET` | 無，`s3` 時必填 | api、worker | bucket 名稱，不由服務建立 |
| `OPENMOT_S3_ENDPOINT_URL` | 未設定 | api、worker | 相容服務的 `http://` 或 `https://` 位址；設定時使用 path-style 定址 |
| `OPENMOT_S3_REGION` | `us-east-1` | api、worker | 簽章用的區域 |
| `AWS_ACCESS_KEY_ID`、`AWS_SECRET_ACCESS_KEY` | 無 | api、worker | 由 boto3 從環境讀取；不寫進設定物件、日誌或錯誤訊息 |

`OPENMOT_STORAGE_DIR` 在 `s3` 模式仍必填，但只放本機暫存：api 用 `staging/`，worker 用 `scratch/`，事後應為空。

#### 用 Compose 啟動

```bash
export S3_ACCESS_KEY=<自訂> S3_SECRET_KEY=<自訂，至少 8 個字元>
docker compose -f deploy/docker/docker-compose.yml -f deploy/docker/docker-compose.weights-volume.yml \
  -f deploy/docker/docker-compose.s3.yml up -d s3
```

缺少兩個變數任一個時，`docker compose config` 會失敗並指出缺哪一個；提交的檔案內沒有預設金鑰。override 加入 `s3` 服務（映像 `rustfs/rustfs:1.0.6`）（只綁在 `127.0.0.1:9000`，埠可用 `OPENMOT_S3_PORT` 改），並讓 api 與 worker 各用**不同**的暫存卷（`api-scratch`、`worker-scratch`），所以兩者之間沒有共用磁碟。bucket 要先建立（預設名稱 `openmot`，可用 `OPENMOT_S3_BUCKET` 改），再啟動其餘服務：

```python
import boto3
boto3.client("s3", endpoint_url="http://127.0.0.1:9000", region_name="us-east-1").create_bucket(Bucket="openmot")
```

（上面的 Python 需要 `AWS_ACCESS_KEY_ID` 與 `AWS_SECRET_ACCESS_KEY` 設成與 `S3_ACCESS_KEY`、`S3_SECRET_KEY` 相同的值。）bucket 不存在時 `/health` 回 `503` 並指出物件儲存不可用。再執行 `up -d redis api worker`。

#### 已驗證與限制

- 已驗證：在這台機器（RTX 5090、Docker Compose）以 `pytest -m "integration or gpu" tests/test_s3_storage_e2e.py -s` 對 MOT17-09-FRCNN 的 60 張影格跑一個真實 Job，六項檢查全數通過：Job `succeeded`；bucket 內的 Result 與中繼檔和以命令列處理同一序列的輸出逐位元組相同；`frames/` 恰有 60 個物件；api 與 worker 的 `/data/storage` 是不同的卷；兩邊本機暫存為空；bucket 被刪除後 `/health` 為 `503` 且不含金鑰。本機 RustFS 的延遲：提交（上傳 60 張）1.0 秒，提交到 `succeeded` 共 3.0 秒。
- 只驗證 RustFS 的釘定版本 `1.0.6`（映像摘要 `sha256:ee717072290bec9b11abfc0365f8310b00df1cd9b3639943edae47bdef05044a`，Apache 2.0）。**沒有**在 MinIO、AWS S3 或其他相容服務上驗證；RustFS 與 MinIO 不是同一個程式，行為可能不同。官方 `minio/minio` 映像拉不到，所以沒有用它。
- Kubernetes 不支援：k8s 清單沒有改，仍用 PersistentVolumeClaim。
- 沒有物件保存期限：影格、Result 與中繼檔會一直留在 bucket，需自行設定生命週期或刪除。
- 逐張傳輸：影格逐張上傳、下載，沒有平行傳輸；一個 600 張的序列就是 600 次請求，延遲數字只量過 60 張的本機情況。
- 孤兒物件與殘留暫存：清理是盡力而為，失敗只記日誌；api 在提交中途失敗或進程被終止時，bucket 內可能留下沒有 Job 的物件；worker 被強制終止時，`scratch/` 可能殘留，同一個 Job 重跑時會在下載前清除。
- 映像大小：`boto3` 讓 api 映像增加約 20.5 MB、worker 映像約 20.6 MB（`docker image inspect` 的 Size）；`docker images` 列出的 api 映像是 763 MB → 849 MB，兩種量法的差異原因沒有查明。
- 以靜態金鑰認證，沒有驗證 IAM 角色或暫時憑證；本機用的是 RustFS 的 root 金鑰，正式環境應建立專屬的存取金鑰。

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

TensorRT worker 另有 `gpu` 與 `integration` 標記的端對端測試，缺 Docker、nvidia runtime、`openmot-weights` 卷或 MOT17 影格時會 skip：

```shell
python -m pytest -m "integration or gpu" tests/test_tensorrt_worker_e2e.py -s
```

它全程使用獨立名稱（compose 專案 `openmot-trt-e2e`、卷 `openmot-trt-e2e-engines`、`openmot-trt-e2e-frames`），`openmot-weights` 只以唯讀使用，結束後只清除這些名稱的資源，不動 `openmot` 專案的容器、卷與映像；設 `OPENMOT_TRT_E2E_KEEP_ENGINES=1` 可保留 engine 卷以省下重建時間。實測結果（RTX 5090、TensorRT 10.13.3.9、MOT17-09 前 60 張影格）：

- 同一個 engine 以命令列連續執行 3 次，Result 與中繼檔逐位元組相同，所以下面的比對可以用逐位元組相同的措辭；這只代表同一機器、同一 engine 的 3 次量測，不保證重建 engine 後仍相同。
- TensorRT worker 處理該 Job 從提交到 `succeeded` 約 2.3 秒；它的 Result 與中繼檔和同一映像、同一 engine 的 `python -m tracking.run --backend tensorrt --engine …` 逐位元組相同，中繼檔含 `"backend": "tensorrt"`。
- `OPENMOT_ENGINE_FILE` 指向不存在的檔案時 worker 啟動失敗，錯誤訊息指名該檔案。
- 在預設映像設 `OPENMOT_BACKEND=tensorrt` 時 worker 以結束碼 1 退出，訊息指出缺少 `tensorrt-cu12` 與 `pip install "openmot-serve[export]"`。這項測試曾揭露舊訊息誤報成「tensorrt_version mismatch，請重建」，已在 `inference/backends.py` 修正。

INT8 另有一份端對端測試：

```shell
python -m pytest -m "integration or gpu" tests/test_tensorrt_int8_worker_e2e.py -s
```

它用 host 的 `models/engines/` 裡已建好的 INT8 engine 與 sidecar（沒有就 skip 並印出建置指令），以 tar 串流複製進專屬的卷，名稱一律以 `openmot-trt-e2e-int8` 開頭（compose 專案 `openmot-trt-e2e-int8`、埠 18002，可用 `OPENMOT_TRT_E2E_INT8_API_PORT` 改），`openmot-weights` 只以唯讀使用，結束後只清除這些名稱的資源。每次先 `docker compose build worker` 更新映像，靠快取很快。實測結果（RTX 5090、TensorRT 10.13.3.9、MOT17-09 前 60 張影格）：

- 同一個 INT8 engine 以命令列連續執行 3 次，Result 與中繼檔逐位元組相同。
- TensorRT worker 以 `OPENMOT_PRECISION=int8` 處理該 Job 從提交到 `succeeded` 約 2.3 秒；Result 與中繼檔和同一映像、同一 engine 的 `python -m tracking.run --backend tensorrt --precision int8 --engine …` 逐位元組相同，中繼檔記錄 `"backend": "tensorrt"` 與 `"precision": "int8"`，日誌是 `detector: backend=tensorrt precision=int8 engine=/models/engines/<engine 檔名>`。
- 未指定精度（預設 `fp16`）卻掛載 INT8 engine 時，worker 在處理任何 Job 之前結束，訊息指名精度不符。
- 驗證時發現舊的 `openmot-worker:tensorrt` 映像是在 INT8 支援之前建的，裡面的程式碼拒絕 `int8`，重建之後才通過；這就是上面要用 `--build` 的原因。

限制：只驗證一個 Job、一個 engine（host 上建好再複製進卷，沒有在映像內重建 INT8 engine）；Kubernetes 仍然沒有 TensorRT 映像。
