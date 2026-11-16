## MODIFIED Requirements

### Requirement: The score summary records how it was produced
分數摘要 SHALL 記錄偵測模型權重的識別資訊、推論後端、推論精度（例如 FP16）、最終追蹤參數、軟體版本與 GPU 型號，使不同次的結果可以被公平比較。

#### Scenario: Summary metadata
- **WHEN** 一次 Benchmark Run 完成
- **THEN** 摘要檔含有上述全部中繼資料，且與該次實際使用的設定一致

## ADDED Requirements

### Requirement: Cached detections are never shared between inference backends
偵測快取 SHALL 將推論後端納入其識別依據：以不同後端產生的偵測結果 SHALL NOT 互相沿用，即使其他設定（權重、精度、輸入尺寸、門檻）完全相同。

#### Scenario: Same settings, different backend
- **WHEN** 兩次執行除了推論後端之外的設定都相同
- **THEN** 第二次執行不使用第一次的快取，而是重新偵測

#### Scenario: Same settings, same backend
- **WHEN** 兩次執行的所有設定（含推論後端）都相同
- **THEN** 第二次執行直接使用快取，不重新偵測
