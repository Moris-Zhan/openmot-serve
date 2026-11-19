## ADDED Requirements

### Requirement: A Compose override runs the service against a local S3-compatible store without a shared disk
Compose 的 S3 override SHALL 加入一個 S3 相容容器（RustFS），並讓 api 與 worker 以 `OPENMOT_STORAGE_BACKEND=s3` 使用它；api 與 worker 的本機暫存 SHALL 掛在**不同**的具名 volume，使兩者之間沒有共用磁碟。S3 伺服器與 bucket 的金鑰 SHALL 由環境變數提供，缺少時渲染設定 SHALL 失敗並指名缺少的變數，且 SHALL NOT 在提交的檔案或映像中帶有預設金鑰。S3 伺服器 SHALL 有健康檢查，api 與 worker SHALL 在它就緒之後才啟動。疊加 S3 override 之前的 Compose 設定 SHALL 不因這個 override 的存在而改變。端對端驗證 SHALL 只使用專屬名稱的容器、卷、埠與專案，唯讀使用權重卷，不動其他專案的資源。

#### Scenario: Default Compose is unchanged
- **WHEN** 渲染不含 S3 override 的 Compose 設定
- **THEN** 內容與提供 S3 override 之前相同，api 與 worker 仍共用同一個 storage volume

#### Scenario: No shared disk with the override
- **WHEN** 渲染疊加了 S3 override 的設定
- **THEN** api 與 worker 的 `/data/storage` 來自兩個不同的具名 volume，兩者都沒有掛載到 S3 伺服器的資料

#### Scenario: Credentials are required
- **WHEN** 渲染疊加了 S3 override 但未提供 S3 金鑰的設定
- **THEN** 渲染失敗並指名缺少的環境變數

#### Scenario: A real Job through RustFS
- **WHEN** 以專屬名稱啟動疊加了 S3 override 的服務，並提交一個真實的序列
- **THEN** Job 進入 `succeeded`，Result 與同一組設定以命令列處理的結果逐位元組相同，bucket 內有影格、`result.txt` 與 `result.meta.json`，api 與 worker 的本機暫存目錄為空

#### Scenario: The verification does not touch other projects' resources
- **WHEN** 端對端驗證執行並結束
- **THEN** 只有專屬名稱（`openmot-s3-e2e` 開頭）的容器、卷與專案被建立與清除，`openmot` 專案的容器、卷與映像不變，權重卷只被唯讀掛載
