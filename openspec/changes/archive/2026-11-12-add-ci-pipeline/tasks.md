# Tasks

## 1. Lint 設定與既有程式碼修正（continuous-integration）

- [x] 1.1 先寫失敗的測試再加設定：`ruff==0.8.4` 加入 `dev` extras，`pyproject.toml` 加入 `[tool.ruff]`（行寬 120、排除 `eval/vendor`）與 `[tool.ruff.lint]`（`select = ["E", "F", "I", "B", "UP"]`）；新增測試執行 `ruff check .` 並要求乾淨，沒有 ruff 時 skip 並說明原因；驗證測試因現有違規而失敗，且設定沒有把 `eval/vendor` 納入檢查
- [x] 1.2 修正既有違規，只做必要變更：`F401`、`E731`、`I001` 以 `ruff check --fix` 處理並檢視差異，`E501` 手動換行，`B008` 若為 FastAPI 的慣用寫法則以設定允許並寫明原因；不重新排版、不改行為；驗證 `ruff check .` 乾淨、完整預設測試全部通過
- [x] 1.3 突變驗證 lint 測試：加入一個未使用的匯入，驗證測試失敗並指出檔案與規則；還原後通過

## 2. 工作流程（continuous-integration）

- [x] 2.1 先寫失敗的測試再建立 `.github/workflows/ci.yml` 的 `lint` 與 `test` 工作：測試以 pyyaml 檢查觸發（push 與 pull_request，沒有 `pull_request_target`）、工作流程預設 `contents: read`、`concurrency` 取消被取代的執行、Python 3.11、CPU 版 PyTorch 的安裝在 `pip install -e` 之前、預設測試指令沒有 GPU 相關標記；用 `actionlint` 驗證語法；驗證測試通過且突變（把觸發改成 `pull_request_target`、移除 `concurrency`）會失敗
- [x] 2.2 在 `test` 工作加入 `kustomize` v5.5.0 與 `kubeconform` v0.6.7 的下載、SHA-256 驗證與版本檢查步驟（缺工具在跑測試前就失敗）；先寫測試檢查這些步驟存在且校驗碼是 64 位十六進位字串、版本與文件一致；驗證測試通過，並在本機以同樣的指令下載並比對校驗碼成功
- [x] 2.3 加入 `build-api` 工作：`push: false` 建置 `deploy/docker/api.Dockerfile`、建置後以容器執行 `python -c "import app.run"` 做冒煙檢查；先寫測試檢查它不推送、不需要寫入權限；驗證測試通過，並在本機以相同的 `docker build` 與冒煙指令建置成功
- [x] 2.4 加入 `publish` 工作：僅在 push 到 `main` 且 `needs: [lint, test, build-api]`、只有此工作有 `packages: write`、清理 runner 磁碟、登入 GHCR、建置並推送 api 與 worker（標籤為 SHA 與 `main`、owner 轉小寫）、worker 不用建置快取；先寫測試檢查條件、`needs`、權限範圍與標籤；驗證測試通過且突變（拿掉 `main` 條件、把 `packages: write` 放到工作流程層級）會失敗
- [x] 2.5 加入跨工作流程的測試：所有第三方 action 都固定到明確版本（沒有 `@main`、`@master`）、沒有 `secrets.` 以外的明文憑證、除 `GITHUB_TOKEN` 外不使用機密；驗證測試通過且突變（把 action 改成 `@main`）會失敗

## 3. 驗證（continuous-integration）

- [x] 3.1 在乾淨的虛擬環境用與工作流程相同的指令（CPU 版 PyTorch、`pip install -e ".[dev,service]"`、`ruff check .`、預設測試）完整重現，記錄通過的測試數與耗時，並確認 Kubernetes 清單測試沒有被 skip
- [x] 3.2 取得使用者同意後推送一個分支，用 `gh run watch` 觀察 `lint`、`test`、`build-api` 在真實 runner 上的結果並記錄耗時；再推送一個含刻意 lint 違規的分支，驗證 `lint` 失敗、`publish` 不執行；`gh` 未登入時改由使用者執行並貼回結果
- [x] 3.3 取得使用者同意後（會在其 GitHub 帳號建立 GHCR 套件並推送約 11 GB）推送到 `main`，驗證 `publish` 成功、兩個映像以 SHA 與 `main` 標籤出現在 GHCR，並記錄 worker 建置的耗時與磁碟使用；失敗時記錄原因而不重試其他方式

## 4. 文件與收尾（continuous-integration）

- [x] 4.1 新增 `docs/ci.md`（繁體中文）：工作流程各工作的用途與觸發、在本機重現 lint、測試與 api 映像建置的指令、映像名稱與取得方式、GHCR 權限、常見失敗與對策、GPU 測試不在 CI 內的說明；驗證文件中的指令照字面執行得到與 CI 相同的結果，文件中的工作名稱與工作流程一致（測試）
- [x] 4.2 更新 README：路線圖 v0.4 的 CI 項目如實勾選（3.2、3.3 未完成時如實標示）、Status 反映 v0.4 進度、加上 CI 狀態說明；驗證 README 的說法與工作流程一致
- [x] 4.3 執行預設的完整測試（不含 `gpu`、`data`、`integration`）、`ruff check .` 與 `openspec validate add-ci-pipeline --strict`；驗證三者皆通過

## Workflow follow-up

- 全部完成後，用 `/opsx:archive` 封存這個變更並把 delta spec 同步到主 spec。
