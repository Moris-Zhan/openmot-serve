# Proposal

## Why

README 開頭只有一大段狀態說明，連到 `docs/service.md`，沒有獨立的快速上手區塊。想啟動並驗證服務的人必須先讀完狀態段落，再自己從 `docs/service.md` 找到「容器部署」那一節。擁有者也問過「怎麼開啟並驗證，說明文件有沒有記載」。說明其實都有，只是在 README 找不到入口。

## What Changes

- 在 `README.md` 的 `## API` 之前新增 `## Quick start` 一節，使用英文（與 README 現有語言一致）。
- 內容只有三件事：以 compose 啟動並確認三個服務都 healthy、用 `/health` 驗證、從別台電腦使用時開 SSH 通道（api 只綁在主機的 `127.0.0.1`）。
- 詳細說明（權重放置、環境變數、疑難排解、完整流程）只放連結指向 `docs/service.md`，不在 README 複製。
- 新增測試：快速上手一節存在、指令指向實際存在的 compose 檔、連結的目標檔案存在、並寫出 loopback 與 SSH 通道。
- 不改程式、不改 compose、不改 `docs/service.md`。

## Capabilities

### New Capabilities

（無）

### Modified Capabilities

（無。這是只改文件的變更，不改變系統的外部行為，因此以 `skip_specs: true` 標記。）

## Impact

- 受影響：`README.md`、新增一個 README 測試檔。
- 前提：`bind-api-port-to-loopback` 尚未合併。快速上手要寫「api 只綁在 127.0.0.1」，所以這個變更必須在它合併之後才能合併，否則 README 會描述尚未生效的行為。實作與測試可以先做，但合併順序要排在它後面。
- 沒有使用者可見的行為變動。
