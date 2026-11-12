# continuous-integration Specification

## Purpose
讓每一次程式碼變更在合併前都自動經過 lint、不需 GPU 的預設測試與映像建置的檢查，並在 `main` 上發佈可部署的映像，使回歸能在 push 之後立刻被發現。

## Requirements

### Requirement: Every push and pull request is linted and tested without a GPU
系統 SHALL 在每次 push 與 pull request 自動執行 lint 與預設測試（排除需要 GPU、資料集或外部服務的測試），且 SHALL 不需要 GPU。lint 違規或任何測試失敗 SHALL 使檢查失敗。

#### Scenario: Clean change
- **WHEN** 推送一個沒有 lint 違規、預設測試全部通過的變更
- **THEN** lint 與測試的檢查都成功

#### Scenario: Lint violation
- **WHEN** 推送一個含有 lint 違規的變更
- **THEN** lint 檢查失敗並指出違規的檔案與規則，變更不被視為通過

#### Scenario: Failing test
- **WHEN** 推送一個使預設測試失敗的變更
- **THEN** 測試檢查失敗並顯示失敗的測試

### Requirement: Manifest checks run instead of being skipped
Kubernetes 清單的渲染與 schema 檢查 SHALL 在自動化檢查中實際執行，不得因為缺少工具而被略過。缺少所需工具時 SHALL 使檢查失敗，而不是靜默通過。

#### Scenario: Tools present
- **WHEN** 自動化檢查執行預設測試
- **THEN** 依賴 `kustomize` 與 `kubeconform` 的測試都實際執行且通過，沒有被 skip

#### Scenario: Tool missing
- **WHEN** 所需的清單工具未安裝成功
- **THEN** 檢查在執行測試之前就失敗，並指出缺少哪個工具

### Requirement: The api image build is verified on every push
系統 SHALL 在每次 push 與 pull request 於乾淨環境建置 api 映像，使損壞的 Dockerfile 或缺少的依賴在合併前被發現。這個建置 SHALL NOT 推送任何映像。

#### Scenario: Dockerfile broken
- **WHEN** 推送一個使 api 映像無法建置的變更
- **THEN** 映像建置檢查失敗

#### Scenario: Pull request
- **WHEN** 在 pull request 上建置 api 映像
- **THEN** 建置成功後不會有任何映像被推送到 registry

### Requirement: Images are published only from the main branch
系統 SHALL 僅在 push 到 `main` 時建置 worker 映像並把 api 與 worker 映像推送到 GHCR，標籤包含 commit 的 SHA。pull request 與其他分支 SHALL NOT 推送映像，也 SHALL NOT 取得推送所需的寫入權限。

#### Scenario: Push to main
- **WHEN** 變更被推送到 `main` 且 lint 與測試都通過
- **THEN** api 與 worker 映像以該 commit 的 SHA 與 `main` 為標籤出現在 GHCR

#### Scenario: Push to another branch
- **WHEN** 變更被推送到 `main` 以外的分支
- **THEN** 不建置 worker 映像，也沒有任何映像被推送

#### Scenario: Checks fail on main
- **WHEN** 推送到 `main` 的變更使 lint 或測試失敗
- **THEN** 不發佈任何映像

### Requirement: The workflow runs with least privilege and pinned dependencies
工作流程 SHALL 預設只有唯讀權限，僅在發佈映像的工作取得套件寫入權限；SHALL NOT 使用 pull request 的 fork 程式碼取得機密的觸發方式；使用的第三方 action 與下載的工具 SHALL 固定到明確的版本，下載的二進位檔 SHALL 驗證校驗碼。

#### Scenario: Inspect the workflow
- **WHEN** 檢查工作流程定義
- **THEN** 預設權限為唯讀、只有發佈工作有 `packages: write`、沒有使用 `pull_request_target`、沒有以浮動分支名稱引用的 action

### Requirement: Superseded runs are cancelled and checks can be reproduced locally
同一個分支上被新 push 取代的執行 SHALL 被取消。每個檢查 SHALL 有文件記載的本機指令，使開發者能在不依賴 CI 的情況下重現相同的結果。

#### Scenario: Two pushes in a row
- **WHEN** 同一分支在前一次執行完成前又收到新的 push
- **THEN** 前一次執行被取消，只有最新的一次繼續

#### Scenario: Reproduce a failure locally
- **WHEN** 開發者依文件在本機執行 lint 與測試的指令
- **THEN** 得到與 CI 相同的通過或失敗結果
