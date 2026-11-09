# runtime-environment Specification

## Purpose
讓使用者在執行任何追蹤或評測之前，先確認目標機器的 GPU、CUDA 與 PyTorch 真的可用，避免在長時間的 Benchmark Run 中途才發現環境問題。

## Requirements
### Requirement: Environment check reports GPU stack status
系統 SHALL 提供一個環境檢查指令，回報 GPU 名稱、驅動版本、PyTorch 版本、PyTorch 所使用的 CUDA 版本，以及 CUDA 是否可用。

#### Scenario: Healthy environment
- **WHEN** 使用者在具備可用 GPU 與相容 PyTorch 的機器上執行環境檢查
- **THEN** 指令列出上述五項資訊，並以結束碼 0 結束

#### Scenario: CUDA is not available
- **WHEN** 使用者在 PyTorch 無法使用 CUDA 的機器上執行環境檢查
- **THEN** 指令明確指出 CUDA 不可用，並以非 0 的結束碼結束

### Requirement: Environment check exercises the GPU
環境檢查 SHALL 在 GPU 上實際執行一次張量運算，並將結果與 CPU 計算結果比對，不得只依賴版本字串判斷環境是否可用。

#### Scenario: GPU computation matches CPU
- **WHEN** 環境檢查在 GPU 上執行一次矩陣運算
- **THEN** GPU 結果與 CPU 結果在容許誤差內一致，檢查才算通過

#### Scenario: GPU architecture not supported by the installed build
- **WHEN** 已安裝的 PyTorch 版本不含目前 GPU 架構的運算核心（kernel），導致運算失敗
- **THEN** 指令回報該失敗與 GPU 型號，並以非 0 的結束碼結束

### Requirement: Environment check enforces the minimum CUDA version
環境檢查 SHALL 在 PyTorch 所使用的 CUDA 版本低於 12.8 時判定失敗，因為目標 GPU（RTX 5090）需要 CUDA 12.8 以上。

#### Scenario: CUDA version below the minimum
- **WHEN** 環境檢查偵測到 PyTorch 使用的 CUDA 版本為 12.4
- **THEN** 指令回報版本過低與所需的最低版本，並以非 0 的結束碼結束
