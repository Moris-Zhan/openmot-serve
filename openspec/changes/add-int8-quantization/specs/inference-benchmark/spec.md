## MODIFIED Requirements

### Requirement: The latency benchmark can target each inference backend
延遲量測指令 SHALL 接受推論後端與（可選的）產物路徑，未指定時使用 `torch` 後端。所有後端與精度（含 `tensorrt` 的 INT8） SHALL 以相同的方式量測：相同的影格來源、預熱與量測張數、兩層延遲的定義與吞吐的定義，使不同後端的數字可以直接比較。純推論層 SHALL 只含該後端推論引擎的執行，不含前處理與後處理。不支援的後端、不支援的後端與精度組合，或產物驗證失敗，SHALL 使指令在載入模型之前或載入時以非 0 的結束碼結束並說明原因，不寫出任何結果檔。

#### Scenario: Same method for every backend
- **WHEN** 使用者以相同的參數分別對 `torch`、`onnx` 與 `tensorrt` 執行量測
- **THEN** 三份結果使用相同的影格、預熱與量測張數，且各自含純推論與端到端兩層延遲及吞吐

#### Scenario: INT8 is measured like FP16
- **WHEN** 使用者以相同的參數分別對 `tensorrt` 的 `fp16` 與 `int8` engine 執行量測
- **THEN** 兩份結果使用相同的影格、預熱與量測張數，並各自記錄精度

#### Scenario: Unsupported combination
- **WHEN** 使用者指定 `tensorrt` 搭配 `fp32`
- **THEN** 指令以非 0 的結束碼結束並說明 `tensorrt` 只支援 `fp16` 與 `int8`，不寫出任何結果檔

#### Scenario: Artifact is missing
- **WHEN** 使用者指定的後端的產物不存在
- **THEN** 指令以非 0 的結束碼結束，錯誤訊息含預期的檔名與建置指令，不寫出任何結果檔
