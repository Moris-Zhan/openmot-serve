# Design

## Context

動機見 proposal.md。目前的事實：

- `tracking/config.py` 有 `SUPPORTED_PRECISIONS = ("fp16", "fp32")` 與對照表 `SUPPORTED_PRECISIONS_BY_BACKEND`（`tensorrt` 只有 `fp16`）。`DetectorSettings`、`RunMetadata`、`jobs/settings.py` 與三個命令列的 `--precision choices` 都由它們驗證，所以改這兩個常數就會一次改到所有入口。
- `inference/build_engine.py` 的 `build_with_tensorrt` 寫死 `BuilderFlag.FP16`，sidecar 寫死 `builder_flags: ["FP16"]`；engine 檔名已含精度，timing cache 以 engine 檔名為準。
- 推論階段的輸入是 `prepare_stage` 產生的 FP32、值域 0–255 的 `1×3×800×1440` 張量，engine 的輸入輸出都是 FP32（ONNX 本身是 FP32），所以 INT8 只改網路內部，不改 I/O 與後處理。
- `eval.split.split_sequence(length)` 給出 Tuning Half（第 1 到 `floor(N/2)` 幀）與 Validation Half；`eval.run` 已用它們調參與計分，偵測快取的鍵含精度與後端。
- TensorRT 10.13.3.9 提供 `IInt8EntropyCalibrator2` 與 `BuilderFlag.INT8`（已在 `tensorrt` 映像內確認）。TensorRT 10 把隱式量化標為棄用，但仍可用；它是這次選擇 PTQ 的代價。
- 上一輪的實測：TensorRT FP16 純推論 4.75 ms、端到端 10.5 ms，前後處理約 6 ms。INT8 只能縮短 4.75 ms 的部分，這是效益的上限。

## Goals / Non-Goals

**Goals:**
- 以不新增套件的方式建出 INT8 engine，校準資料沒有洩漏到 Validation Half，且校準過程可重現、可由 sidecar 追溯。
- 在這台機器上量出 FP16 與 INT8 的準確度與速度，以事先寫好的規則標示結論。
- 預設行為完全不變：沒有指定精度時，建置指令、檔名與 sidecar 的內容與現在相同。

**Non-Goals:**
- 不做 QDQ 顯式量化、逐層敏感度分析、量化感知訓練，也不手動指定哪些層保留 FP16。
- 不量測 GPU 記憶體，README 的 GPU memory 欄維持 TBD。
- 不改 worker、Dockerfile、compose、k8s；服務以 INT8 端對端執行不在這次驗證。`OPENMOT_PRECISION=int8` 因共用對照表而被設定接受，這是共用驗證的副作用，不是承諾。
- 不為了讓 INT8 通過門檻而調校準設定（見 D5）。

## Decisions

**D1：TensorRT PTQ，entropy calibrator，旗標為 INT8 搭配 FP16。**
沿用 `build_engine`，在 `BuildSettings` 加 `precision`（`fp16` 或 `int8`）與校準設定；`int8` 時設 `BuilderFlag.INT8` 與 `BuilderFlag.FP16`，讓無法量化的層退回 FP16，而不是退回 FP32。「INT8 engine」在本專案的意思就是「INT8 搭配 FP16 後備」，文件要寫明。替代方案是 ONNX QDQ：控制更細，但要多一套套件與產物，範圍明顯變大，已被排除。

**D2：校準影格來自 Tuning Half，確定性均勻抽樣，預設 512 張。**
新增 `inference/calibration.py`：對 7 個 FRCNN 序列，用 `split_sequence` 取各自的 Tuning Half，把所有影格依（序列名、幀號）排序成一個清單，再以固定步長取 512 張（步長為 `len(清單) // 512`）。不用亂數，所以相同的資料與參數得到相同的清單，清單的 sha256 寫進 sidecar。前處理直接用 `prepare_stage`，使校準看到的張量與推論時完全相同。資料根目錄預設 `data/MOT17`，找不到序列或可用影格少於要求時在建置之前失敗，不寫任何檔案。512 是這次的假設，不是調校結果；選它是因為 TensorRT 文件建議數百張代表性影格，且校準時間仍短。

**D3：校準快取與 sidecar。**
校準器的快取寫在 engine 旁邊的 `<engine>.calib`，sidecar 新增 `calibration` 區塊：校準器名稱、影格張數、挑選清單的 sha256、快取的 sha256、資料來源的描述（`MOT17 train FRCNN Tuning Half`）。`describe_artifact` 把這個區塊放進 `build`，所以 Benchmark Run 與延遲量測的摘要自然帶上校準識別。載入時的驗證項目不變（權重、ONNX、輸入尺寸、TensorRT 版本、GPU、精度）；校準資料不納入載入驗證，因為它不影響 engine 能不能在目前環境載入。目標 engine 已存在時的處理沿用現有行為，不新增覆寫語意。

**D4：精度名稱與驗證。**
`SUPPORTED_PRECISIONS` 加入 `"int8"`，`tensorrt` 的支援精度改為 `("fp16", "int8")`。`RunMetadata` 與設定的驗證因共用常數而接受 `int8`；既有的錯誤訊息格式不變，只是 `tensorrt` 列出兩種精度。`torch` 與 `onnx` 搭配 `int8` 仍被對照表拒絕。偵測快取的鍵已含精度，INT8 不會沿用 FP16 的偵測。

**D5：驗收規則事先寫死，且不看結果調參。**
這次的準確度規則是：合併 HOTA 與 PyTorch FP16（README 的 FP16 列，50.78）相差在 1.0 以內標「建議使用」，否則標「不建議」。校準設定（張數、抽樣規則、旗標）在第一次建置前固定，之後不因 Validation Half 的結果更動；若有人要調整，必須是另一個變更並說明理由。同時記錄對 TensorRT FP16（50.85）的差，因為兩者是同一個執行環境。對照用既有的完整 Benchmark Run（冷快取）與 `eval.latency` 連續兩次，措辭沿用上一輪的做法，只報實際量到的數字。

**D6：測試分層。**
預設測試用假校準器與假 TensorRT 模組覆蓋：抽樣規則（只含 Tuning Half、可重現、張數不足時失敗）、旗標組合、sidecar 內容、缺資料時不寫檔、對照表與錯誤訊息、`describe_artifact`、文件與數字的一致性。需要 GPU、TensorRT 與 MOT17 的真實建置與評測以 `gpu` 與 `data` 標記，缺任何一項就略過並說明；它們不進 CI（沒有 GPU）。

## Risks / Trade-offs

- [INT8 在 YOLOX-X 的偵測頭上掉分過多] → 這是可能的結果，不是失敗：規則已寫好，超過 1.0 就標「不建議」並如實記錄；FP16 後備只能減輕，不能保證。
- [RTX 5090（Blackwell）上 INT8 沒有明顯加速，或端到端幾乎不變] → 前後處理約 6 ms 佔端到端近六成，Amdahl 上限本來就有限；兩層延遲都報，文件不得把純推論的倍數說成整體的倍數。
- [`IInt8EntropyCalibrator2` 在 TensorRT 10 被標為棄用] → 版本已固定在 10.13.3.9；升級 TensorRT 時需要重新評估，文件寫明這個依賴。
- [同一組輸入重建的 INT8 engine 的偵測可能略有不同（tactic 選擇）] → 沿用上一輪的方法：先量測同一個 engine 重複執行是否逐位元組相同；比較準確度時用同一個 engine，不拿兩次重建的結果互比。
- [校準 512 張不夠或過多] → 不在這次調整；數字照實記錄，後續若要研究是獨立的變更。
- [`int8` 被 worker 設定接受但未驗證] → 文件如實標示「設定接受，服務端對端未驗證」，不寫成支援。

## Open Questions

- 無會改變規格、做法或任務拆分的問題。
