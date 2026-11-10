# 非同步追蹤服務（Service）

這個服務讓你用 HTTP 提交一段影格序列，不必等追蹤完成，之後再查詢進度、取回 MOTChallenge 格式的結果。流程是：

1. `POST /jobs` 上傳影格 zip，立刻拿到 job ID（Job 進入 `queued`）。
2. worker 取出 Job 執行追蹤（`running`），結束後變成 `succeeded` 或 `failed`。
3. `GET /jobs/{job_id}` 查狀態，成功後 `GET /jobs/{job_id}/result` 取結果。

> 本文目前涵蓋 HTTP API 與上傳規則；worker 與容器部署的說明由後續任務補齊。

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
