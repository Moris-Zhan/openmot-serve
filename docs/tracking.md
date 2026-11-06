# 追蹤指令（Tracking）

`python -m tracking.run` 會對一個影格資料夾逐格做人物偵測（YOLOX-X），再用 ByteTrack 串接成軌跡，輸出 MOTChallenge 格式的結果檔。

## 前置條件

- 已建立 conda 環境 `openmot` 並安裝專案（`pip install -e ".[dev]"`）。
- 權重檔位於 `models/yolox_x.pth`（載入前會驗證 SHA-256，不符即拒絕載入）。
- 影格資料夾內為連續編號的影像，例如 `000001.jpg`、`000002.jpg`……。副檔名可為 `.jpg`、`.jpeg`、`.png`，其他檔案會被忽略。

## 基本用法

先產生一個合成影格資料夾（20 張純灰影像，僅用來確認指令可執行）：

```bash
python -c "
import cv2, numpy as np
from pathlib import Path
folder = Path('/tmp/openmot_demo/frames')
folder.mkdir(parents=True, exist_ok=True)
for number in range(1, 21):
    cv2.imwrite(str(folder / f'{number:06d}.jpg'), np.full((540, 960, 3), 128, np.uint8))
"
```

執行追蹤：

```bash
python -m tracking.run /tmp/openmot_demo/frames --output /tmp/openmot_demo/out/demo.txt
```

成功時結束碼為 0，並印出 `wrote N rows for M frames to ...`。純灰影像中沒有人，所以 `N` 為 0，結果檔為空，這是正常的。

## 參數

| 參數 | 說明 | 預設 |
|---|---|---|
| `frames_dir` | 影格資料夾（位置參數） | 必填 |
| `--output` | 結果檔路徑，父資料夾不存在時會自動建立 | 必填 |
| `--config` | TOML 設定檔，見下節 | 無（使用預設值） |
| `--precision` | 推論精度，`fp16` 或 `fp32` | `fp16` |
| `--weights` | 權重檔路徑 | `models/yolox_x.pth` |
| `--device` | torch 裝置，例如 `cuda`、`cpu` | 有 CUDA 時為 `cuda` |

`fp16` 需要 CUDA 裝置；在 CPU 上請改用 `--precision fp32`。

## 設定檔

設定檔只有一個 `[tracker]` 表，三個鍵都可省略，省略時使用預設值：

```toml
[tracker]
detection_threshold = 0.6   # 高低信心的分界，範圍 0.1 到 1
match_threshold = 0.9       # 第一階段匹配的 IoU 成本上限，範圍 (0, 1]
track_buffer = 30           # Track 遺失後保留的幀數，至少 1
```

存成檔案後使用：

```bash
cat > /tmp/openmot_demo/tracker.toml <<'EOF'
[tracker]
detection_threshold = 0.5
match_threshold = 0.8
track_buffer = 45
EOF
python -m tracking.run /tmp/openmot_demo/frames --output /tmp/openmot_demo/out/custom.txt --config /tmp/openmot_demo/tracker.toml
```

未知的區段或鍵、型別錯誤、超出範圍的值都會被拒絕，結束碼為 2。

## 輸出

每次執行會寫出兩個檔案：

- 結果檔（如 `demo.txt`）：每行 `frame,id,left,top,width,height,confidence,-1,-1,-1`，Frame 與 Identity 皆從 1 起算。同一次執行內 Identity 不會被重用。
- 中繼檔（如 `demo.meta.json`）：記錄實際使用的追蹤參數、權重識別與 SHA-256、推論精度，可用來重現該次結果。

```bash
cat /tmp/openmot_demo/out/demo.meta.json
```

兩個檔案都先寫成暫存檔再改名，執行失敗時不會留下半成品，也不會覆蓋先前的結果。

## 錯誤與結束碼

| 結束碼 | 情況 |
|---|---|
| 0 | 成功 |
| 1 | 資料夾不存在、沒有影像、檔名不是數字、編號不連續、影格無法讀取、權重缺失或雜湊不符、`fp16` 用在 CPU |
| 2 | 設定檔不合法 |

驗證資料夾不存在時的行為：

```bash
python -m tracking.run /tmp/openmot_demo/missing --output /tmp/openmot_demo/out/none.txt; echo "exit=$?"
```

會印出 `error: frame folder not found: ...`，結束碼為 1，且不會建立輸出檔。

## 相同輸入的可重現性

相同影格、相同設定、相同精度、相同機器上重跑，結果檔逐位元組相同。追蹤本身是純函式，不依賴隨機性。
