## ADDED Requirements

### Requirement: The TensorRT worker can serve an INT8 engine
TensorRT 的 compose override SHALL 讓 worker 的精度可由環境變數 `OPENMOT_PRECISION` 指定，未設定時為 `fp16`，且未設定時渲染出的設定 SHALL 與提供這個選項之前相同。以 `int8` 啟動並掛載對應的 INT8 engine 時，worker SHALL 通過產物的載入驗證、處理一個真實的 Job 到 `succeeded`，worker 的日誌與 Job 的中繼檔 SHALL 記錄後端為 `tensorrt`、精度為 `int8`。同一個 engine 以命令列處理同一個序列的 Result 與中繼檔 SHALL 與 worker 的逐位元組相同。精度與 engine 不符（例如以 `int8` 啟動卻掛載 FP16 engine，或未指定精度卻掛載 INT8 engine）時，worker SHALL 在處理任何 Job 之前結束並指名不符的項目。端對端驗證 SHALL 只使用專屬名稱的容器、卷與映像，唯讀使用權重卷，不動其他專案的資源。

#### Scenario: Default precision is unchanged
- **WHEN** 渲染疊加了 TensorRT override、且未設定 `OPENMOT_PRECISION` 的 compose 設定
- **THEN** worker 的環境變數為 `OPENMOT_BACKEND=tensorrt` 與 `OPENMOT_PRECISION=fp16`，與提供精度選項之前相同

#### Scenario: Precision is selected from the environment
- **WHEN** 渲染時設定 `OPENMOT_PRECISION=int8`
- **THEN** worker 的環境變數為 `OPENMOT_PRECISION=int8`，api 與 Redis 的設定不變

#### Scenario: An INT8 job succeeds
- **WHEN** worker 以 `int8` 與通過驗證的 INT8 engine 啟動，使用者提交一個影格序列
- **THEN** Job 進入 `succeeded`，Result 可取回，worker 日誌顯示 `backend=tensorrt precision=int8` 與 engine 路徑

#### Scenario: The served result equals the command line
- **WHEN** 以同一個 INT8 engine 在命令列處理同一個序列
- **THEN** worker 的 Result 與命令列的 Result 逐位元組相同，中繼檔也相同，且中繼檔記錄精度為 `int8`

#### Scenario: Precision and engine disagree
- **WHEN** worker 在未指定精度（預設 `fp16`）的情況下啟動，但掛載的是 INT8 engine
- **THEN** worker 在處理任何 Job 之前以非 0 的結束碼結束，錯誤訊息指名精度不符

#### Scenario: The verification does not touch other projects' resources
- **WHEN** 端對端驗證執行並結束
- **THEN** 只有專屬名稱（`openmot-trt-e2e` 開頭）的容器、卷與映像被建立與清除，`openmot` 專案的容器、卷與映像不變，權重卷只被唯讀掛載
