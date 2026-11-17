## MODIFIED Requirements

### Requirement: Configuration names and defaults match the compose deployment
儲存目錄、Redis 位址、權重路徑、精度、推論後端（預設 `torch`）、大小上限與逾時 SHALL 由清單中產生的設定提供，且與 compose 部署使用相同的環境變數名稱與預設值。選用的 `OPENMOT_ENGINE` SHALL NOT 出現在預設的設定中。

#### Scenario: Config matches compose
- **WHEN** 渲染清單並與 compose 的環境變數比對
- **THEN** compose 設定的每個變數都存在於產生的設定中且值相同，並且載入後等於程式的預設值

#### Scenario: Backend defaults to PyTorch
- **WHEN** 渲染清單並以產生的設定載入 worker 的設定
- **THEN** 推論後端是 `torch`，設定中沒有 `OPENMOT_ENGINE`
