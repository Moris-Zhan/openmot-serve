# TrackEval（內嵌修補版）

本目錄是 [TrackEval](https://github.com/JonathonLuiten/TrackEval) 的精簡複本，授權為 MIT，授權全文見同目錄的 `LICENSE`。

## 來源

- 儲存庫：`JonathonLuiten/TrackEval`
- 固定 commit：`12c8791b303e0a0b50f753af204249e622d0281a`（2022-11-29）

## 為什麼內嵌，而不是用 pip 安裝

- 上游程式碼在 19 個檔案共 81 處使用 `np.float`、`np.int`、`np.bool` 等別名，這些別名在 numpy 1.24 之後已被移除，而本專案使用 numpy 2.x，原版會直接丟出 `AttributeError`。
- 上游已多年沒有更新，也沒有發佈到 PyPI。
- 上游的 `requirements.txt` 固定在 numpy 1.18 等舊版本，不能原樣安裝。

## 複製的檔案

只保留 MOT17 行人評測需要的部分，其他資料集與指標（KITTI、MOTS、TAO、BURST、YouTube-VIS、plotting 等）都沒有帶入：

- `eval.py`、`_timing.py`、`utils.py`
- `datasets/_base_dataset.py`、`datasets/mot_challenge_2d_box.py`
- `metrics/_base_metric.py`、`metrics/hota.py`、`metrics/clear.py`、`metrics/identity.py`、`metrics/count.py`

## 修改內容

1. 三個 `__init__.py` 改寫成只匯出上述類別（上游版本會匯入所有資料集，需要 pycocotools、matplotlib 等額外套件）。
2. 其餘檔案只做一種文字替換：`np.float` → `float`、`np.int` → `int`、`np.bool` → `bool`、`np.object` → `object`、`np.str` → `str`。這些別名原本就等於 Python 內建型別，因此行為不變，共 12 處。

升級或重新同步時，請從上述固定 commit 重新複製並重做以上兩項修改。
