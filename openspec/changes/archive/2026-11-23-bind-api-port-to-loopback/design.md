# Design

## Context

api 是 compose 裡唯一發佈埠的服務。目前映射是 `"${OPENMOT_API_PORT:-8000}:8000"`。S3 與監控的 compose 已使用 `"127.0.0.1:${VAR:-預設}:容器埠"` 的寫法，並有對應測試。現有測試 `test_only_the_api_publishes_a_port` 只檢查容器埠是 8000，沒有檢查位址。

## Goals / Non-Goals

**Goals:**
- api 只能從這台機器本身連線，其他機器要透過 SSH 通道。
- 與 S3、監控的 compose 保持同一種寫法。

**Non-Goals:**
- 不加驗證、TLS、速率限制，也不處理上傳大小在整份請求收完後才檢查的問題；這些只在公開服務時才需要，不在本次範圍。
- 不改 Kubernetes 清單；k3s 驗證已擱置。
- 不改 api 程式裡的監聽位址；容器內仍要監聽 `0.0.0.0`，否則映射進不去。

## Decisions

- **位址寫死為 `127.0.0.1`，不做成變數。** 好處是設定錯誤的空間最小，也不會有人無意間把它改成 `0.0.0.0`。替代方案是新增 `OPENMOT_API_BIND`，保留彈性；但目前沒有需要對外開放的情境，多一個變數就是多一個能被設錯的地方。之後真的要公開時，應該連同驗證與 TLS 另開一個變更。
- **用測試守住，而不是只改檔案。** 在 `tests/test_compose_file.py` 檢查解析後的設定中每個發佈埠的 `host_ip`。沿用既有的 `docker compose config --format json`，不需要啟動容器。另外參數化檢查 `OPENMOT_API_PORT` 改動時位址不變。
- **只改基底 compose。** 其他 compose 檔（tensorrt、s3、monitoring、weights-volume）沒有重新定義 api 的 `ports`，所以會自動繼承。已用搜尋確認。

## Risks / Trade-offs

- [以前直連區域網路的用法會失效] → 擁有者已確認只用 SSH 通道；文件補上通道指令。
- [宿主機上若有其他程式佔用 127.0.0.1:8000] → 與改動前相同，仍可用 `OPENMOT_API_PORT` 改埠。
- [Docker 對 loopback 發佈的行為取決於防火牆與 Docker 版本] → 不假設，驗證時實際從另一台機器連線確認被拒絕；若無法從另一台機器測試，就在任務與回報裡明說這一項未驗證。
