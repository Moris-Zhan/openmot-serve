#!/bin/bash

set -uo pipefail

# --- GPU 使用率彩色進度條（nvitop 風格）---
# 顏色只在真的接到終端機時輸出 ANSI escape，避免污染 log 檔或 pipe 到其他程式時混入亂碼。
if [ -t 1 ]; then
    C_GREEN=$'\033[32m'
    C_YELLOW=$'\033[33m'
    C_RED=$'\033[31m'
    C_BLUE=$'\033[34m'
    C_RESET=$'\033[0m'
else
    C_GREEN=""; C_YELLOW=""; C_RED=""; C_BLUE=""; C_RESET=""
fi

# 依門檻回傳顏色：<30 綠、30-70 黃、>70 紅（GPU 使用率、顯存佔用共用同一套門檻）
gpu_color() {
    local pct="$1"
    if [ "$pct" -lt 30 ]; then
        echo "$C_GREEN"
    elif [ "$pct" -lt 70 ]; then
        echo "$C_YELLOW"
    else
        echo "$C_RED"
    fi
}

# 溫度門檻：<60 綠、60-80 黃、>80 紅
temp_color() {
    local t="$1"
    if [ "$t" -lt 60 ]; then
        echo "$C_GREEN"
    elif [ "$t" -lt 80 ]; then
        echo "$C_YELLOW"
    else
        echo "$C_RED"
    fi
}

# 畫一條寬度 $2、填滿比例 $1%（0-100）的進度條，依 gpu_color 上色
draw_bar() {
    local pct="$1" width="$2" filled empty color bar
    filled=$(( pct * width / 100 ))
    # pct 是 0 以外的值時至少填滿 1 格，不然低於「1/width」(這裡是 5%) 的非零進度會無條件捨去
    # 成 0 格、整條 bar 看起來跟真的 0% 一模一樣。
    (( filled == 0 && pct > 0 )) && filled=1
    (( filled > width )) && filled=$width
    (( filled < 0 )) && filled=0
    empty=$(( width - filled ))
    color=$(gpu_color "$pct")
    printf -v bar '%*s' "$filled" ''
    printf '%s%s%s' "$color" "${bar// /█}" "$C_RESET"
    printf -v bar '%*s' "$empty" ''
    printf '%s' "${bar// /░}"
}

# 跟 draw_bar 一樣畫寬度 $2、填滿比例 $1% 的進度條，但顏色固定用 $3（不依 gpu_color 門檻變色）。
# 訓練 step 進度條用這個、固定畫藍色，跟依負載變色的 GPU 使用率/顯存 bar 意義不同，
# 所以另外寫一個函式而不是動 draw_bar。
draw_bar_fixed_color() {
    local pct="$1" width="$2" color="$3" filled empty bar
    filled=$(( pct * width / 100 ))
    # 同 draw_bar：pct>0 時至少填滿 1 格，避免低於「1/width」的非零進度整條看起來像 0%。
    (( filled == 0 && pct > 0 )) && filled=1
    (( filled > width )) && filled=$width
    (( filled < 0 )) && filled=0
    empty=$(( width - filled ))
    printf -v bar '%*s' "$filled" ''
    printf '%s%s%s' "$color" "${bar// /█}" "$C_RESET"
    printf -v bar '%*s' "$empty" ''
    printf '%s' "${bar// /░}"
}

# 抓「這一瞬間」的整體系統 CPU 使用率（不是單一程序的 %CPU）：讀兩次 /proc/stat 各欄位的
# 累計 tick 數，中間隔 0.2 秒，用差值算出這段時間裡 CPU 忙碌的比例。/proc/stat 第一行
# "cpu  user nice system idle iowait irq softirq steal guest guest_nice" 裡，
# busy = 總 tick 差 - (idle+iowait) 的 tick 差；iowait 不算忙碌（CPU 其實在等 I/O）。
cpu_usage_pct() {
    local u1 n1 s1 i1 io1 irq1 sirq1 steal1 u2 n2 s2 i2 io2 irq2 sirq2 steal2
    local total1 total2 idle1 idle2 diff_total diff_idle
    read -r _ u1 n1 s1 i1 io1 irq1 sirq1 steal1 _ < /proc/stat
    sleep 0.2
    read -r _ u2 n2 s2 i2 io2 irq2 sirq2 steal2 _ < /proc/stat
    total1=$(( u1 + n1 + s1 + i1 + io1 + irq1 + sirq1 + steal1 ))
    total2=$(( u2 + n2 + s2 + i2 + io2 + irq2 + sirq2 + steal2 ))
    idle1=$(( i1 + io1 ))
    idle2=$(( i2 + io2 ))
    diff_total=$(( total2 - total1 ))
    diff_idle=$(( idle2 - idle1 ))
    if [ "$diff_total" -le 0 ]; then
        echo 0
    else
        echo $(( 100 * (diff_total - diff_idle) / diff_total ))
    fi
}

snapshot() {    

    echo
    echo "--- CPU / 系統記憶體 ---"
    local sys_bar_width=20 cpu_pct
    cpu_pct=$(cpu_usage_pct)
    printf "CPU  ["
    draw_bar "$cpu_pct" "$sys_bar_width"
    printf "] %3s%%\n" "$cpu_pct"

    local mem_total mem_used mem_pct mem_total_gib mem_used_gib
    read -r mem_total mem_used < <(free -b | awk '/^Mem:/{print $2, $3}')
    mem_pct=0
    if [ "${mem_total:-0}" -gt 0 ] 2>/dev/null; then
        mem_pct=$(( mem_used * 100 / mem_total ))
    fi
    mem_total_gib=$(awk -v b="${mem_total:-0}" 'BEGIN{printf "%.1f", b/1024/1024/1024}')
    mem_used_gib=$(awk -v b="${mem_used:-0}" 'BEGIN{printf "%.1f", b/1024/1024/1024}')
    printf "MEM  ["
    draw_bar "$mem_pct" "$sys_bar_width"
    printf "] %3s%%  (%sGi / %sGi)\n" "$mem_pct" "$mem_used_gib" "$mem_total_gib"

    echo
    echo "--- 磁碟空間（共用儲存）---"
    local disk_target disk_line disk_size disk_used disk_pct disk_mount
    disk_target="/mnt/moris"
    df -h "$disk_target" >/dev/null 2>&1 || disk_target="$PROJECT_DIR"
    disk_line=$(df -h "$disk_target" 2>/dev/null | tail -1)
    disk_size=$(awk '{print $2}' <<< "$disk_line")
    disk_used=$(awk '{print $3}' <<< "$disk_line")
    disk_pct=$(awk '{print $5}' <<< "$disk_line" | tr -d '%')
    disk_mount=$(awk '{print $6}' <<< "$disk_line")
    printf "DISK ["
    draw_bar "${disk_pct:-0}" "$sys_bar_width"
    printf "] %3s%%  (%s / %s)  掛載點：%s\n" "${disk_pct:-0}" "$disk_used" "$disk_size" "$disk_mount"

    echo
    echo "--- GPU ---"
    if command -v nvidia-smi >/dev/null 2>&1; then
        local gpu_csv bar_width=20
        # nounits 拿掉 "MiB"/"%" 等單位方便直接做數字運算；用 sed 把 ", " 換成 "|"
        # 而不是直接用逗號當分隔字元，這樣 GPU 名稱裡的空白（如 "NVIDIA GeForce RTX 4090"）
        # 才不會被拆成好幾個欄位。
        gpu_csv=$(nvidia-smi --query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu \
            --format=csv,noheader,nounits 2>/dev/null | sed -E 's/, */|/g')
        if [ -n "$gpu_csv" ]; then
            local idx name util mem_used mem_total temp mem_pct
            while IFS='|' read -r idx name util mem_used mem_total temp; do
                [ -z "$idx" ] && continue
                mem_pct=0
                if [ "$mem_total" -gt 0 ] 2>/dev/null; then
                    mem_pct=$(( mem_used * 100 / mem_total ))
                fi
                printf "GPU%s %-28s [" "$idx" "$name"
                draw_bar "$util" "$bar_width"
                printf "] %3s%%  MEM[" "$util"
                draw_bar "$mem_pct" "$bar_width"
                printf "] %6sMiB / %6sMiB  %s%s°C%s\n" \
                    "$mem_used" "$mem_total" "$(temp_color "$temp")" "$temp" "$C_RESET"
            done <<< "$gpu_csv"
        else
            echo "讀不到 GPU 資訊（nvidia-smi 沒有輸出）"
        fi
    else
        echo "找不到 nvidia-smi"
    fi

    echo "=================================================================="
}

if [ "${1:-}" = "-w" ] || [ "${1:-}" = "--watch" ]; then
    while true; do
        clear
        snapshot
        sleep 2
    done
else
    snapshot
fi
