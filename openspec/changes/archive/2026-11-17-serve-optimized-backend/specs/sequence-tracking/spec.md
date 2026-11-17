## MODIFIED Requirements

### Requirement: Tracking parameters are configurable and recorded
系統 SHALL 允許使用者指定偵測信心門檻、匹配門檻與 Track 保留幀數，並將實際使用的參數、偵測模型權重的識別資訊與推論精度寫入與 Result 並列的中繼檔。推論後端不是 `torch` 時，中繼檔 SHALL 另記錄後端名稱；後端是 `torch` 時，中繼檔的內容 SHALL 與支援後端選項之前逐位元組相同。

#### Scenario: Custom parameters
- **WHEN** 使用者以設定檔指定不同的偵測信心門檻
- **THEN** 追蹤使用該門檻，且中繼檔記錄該值

#### Scenario: Default parameters
- **WHEN** 使用者未指定任何追蹤參數
- **THEN** 系統使用預設值，且中繼檔記錄實際使用的預設值

#### Scenario: Non-PyTorch backend is recorded
- **WHEN** 以 `tensorrt` 或 `onnx` 後端產生 Result
- **THEN** 中繼檔記錄該後端名稱

#### Scenario: PyTorch metadata is unchanged
- **WHEN** 以 `torch` 後端（預設）產生 Result
- **THEN** 中繼檔沒有後端欄位，內容與支援後端選項之前逐位元組相同
