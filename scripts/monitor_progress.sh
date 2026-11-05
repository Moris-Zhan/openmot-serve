#!/bin/bash
# ==================================================================
# monitor_progress.sh — 自動偵測 GPU 上的程序並監控訓練進度
# ==================================================================
#
# 用法：
#   ./scripts/monitor_progress.sh               印一次快照就結束
#   ./scripts/monitor_progress.sh -w            watch 模式，每 10 秒刷新（Ctrl+C 離開）
#   ./scripts/monitor_progress.sh -w -n 5       watch 模式，每 5 秒刷新
#   ./scripts/monitor_progress.sh -p 12345      只看指定 PID（若是多卡訓練的某個 rank，顯示整個訓練）
#   ./scripts/monitor_progress.sh -h            顯示這段說明
#
# 環境變數：
#   FINETUNE_LOG   選用。GPU 上沒有任何程序時，改從這個日誌讀最後一次的進度條與 loss。
#
# 顯示內容：
#   每個 GPU 程序一個區塊（多卡訓練的多個 rank 會合併成一個）：PID、使用者、執行時間、
#   CPU/MEM、使用的 GPU 與顯存、指令、訓練進度條、最新 train/eval loss、最新 checkpoint；
#   最後是整體 CPU / 記憶體 / 磁碟 / GPU 狀態。
#
# 注意事項：
#   - 主機與容器內都可執行：優先用 nvidia-smi 列出的 PID；容器內 nvidia-smi 常列不出 PID、
#     或列出的是主機 PID（跟容器內 /proc 對不上），這時改掃 /proc 找開著 /dev/nvidiaN 的程序。
#   - 讀其他程序的 /proc/<pid>/fd、environ、cwd 需要跟該程序同一個使用者或 root。
#   - 進度條是從訓練程序的 stdout/stderr 讀的，輸出必須導向「檔案」
#     （例如 nohup python train.py > train.log 2>&1 &）；導向終端機或 pipe 時讀不到。
#   - 需要 bash 4+ 與 GNU find（Ubuntu 預設即有）。

set -uo pipefail

usage() {
    # 直接印出檔案開頭那段註解，說明只維護一份
    awk 'NR == 1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "$0"
}

# ==================================================================
# 區塊 1：參數解析
# ==================================================================
WATCH=0
INTERVAL=10
TARGET_PID=""
while [ $# -gt 0 ]; do
    case "$1" in
        -w|--watch) WATCH=1 ;;
        -n) INTERVAL="${2:-}"; shift ;;
        -p) TARGET_PID="${2:-}"; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "未知的參數：$1"; echo; usage; exit 1 ;;
    esac
    shift
done
if ! [[ "$INTERVAL" =~ ^[0-9]+$ ]] || [ "$INTERVAL" -lt 1 ]; then
    echo "-n 必須是正整數秒數：$INTERVAL"; exit 1
fi
if [ -n "$TARGET_PID" ] && ! [[ "$TARGET_PID" =~ ^[0-9]+$ ]]; then
    echo "-p 必須是 PID 數字：$TARGET_PID"; exit 1
fi

LOG_FILE="${FINETUNE_LOG:-}"
# 每個程序的輸出只讀尾端 256KB：訓練跑久了 log 會長到幾百 MB，不能每次快照都整份掃；
# 4KB 又常常只剩 eval 進度條、看不到訓練進度，256KB 才涵蓋得到「訓練進度條＋一整段 eval」。
TAIL_BYTES=262144
CMD_MAX_LEN=150

# ==================================================================
# 區塊 2：顏色與進度條（nvitop 風格）
# ==================================================================
# 顏色只在真的接到終端機時輸出 ANSI escape，避免污染 log 檔或 pipe 到其他程式時混入亂碼。
if [ -t 1 ]; then
    C_GREEN=$'\033[32m'
    C_YELLOW=$'\033[33m'
    C_RED=$'\033[31m'
    C_BLUE=$'\033[34m'
    C_BOLD=$'\033[1m'
    C_RESET=$'\033[0m'
else
    C_GREEN=""; C_YELLOW=""; C_RED=""; C_BLUE=""; C_BOLD=""; C_RESET=""
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

# ==================================================================
# 區塊 3：/proc 小工具
# ==================================================================
# 父程序 PID。/proc/<pid>/stat 第 2 欄是 "(comm)"，comm 本身可能含空白或括號，
# 所以從最後一個 ") " 之後再切欄位，不能直接用 awk '{print $4}'。
ppid_of() {
    local stat rest ppid_val
    stat=$(cat "/proc/$1/stat" 2>/dev/null) || return 1
    rest="${stat##*) }"
    read -r _ ppid_val _ <<< "$rest"
    echo "$ppid_val"
}

# 讀程序啟動時的環境變數值（例如 RANK、LOCAL_RANK）；讀不到時回傳空字串
env_value() {
    tr '\0' '\n' < "/proc/$1/environ" 2>/dev/null | sed -n "s/^$2=//p" | head -1
}

# 程序的完整指令（NUL 分隔換成空白）
cmdline_str() {
    tr '\0' ' ' < "/proc/$1/cmdline" 2>/dev/null | sed 's/ *$//'
}

# 從程序 cmdline 取出 --<name> 的值，支援 "--name value" 與 "--name=value" 兩種寫法
cmdline_arg() {
    local pid="$1" name="$2" arg next=0
    while IFS= read -r arg; do
        if [ "$next" -eq 1 ]; then
            echo "$arg"; return
        fi
        case "$arg" in
            "--$name") next=1 ;;
            "--$name="*) echo "${arg#--$name=}"; return ;;
        esac
    done < <(tr '\0' '\n' < "/proc/$pid/cmdline" 2>/dev/null)
}

# 判斷程序是否開著 /dev/nvidiaN：0 = 有、1 = 沒有、2 = 沒權限看不到 fd。
# CUDA context 建立後會一直開著 /dev/nvidiaN，所以這可以當作「真的在用 GPU」的證據。
# 只比對 /dev/nvidia 後面直接接數字的，排除 nvidiactl、nvidia-uvm 這些共用裝置。
nvidia_fd_state() {
    local pid="$1"
    ls "/proc/$pid/fd" >/dev/null 2>&1 || return 2
    if [ -n "$(find "/proc/$pid/fd" -maxdepth 1 -lname '/dev/nvidia[0-9]*' -print -quit 2>/dev/null)" ]; then
        return 0
    fi
    return 1
}

# ==================================================================
# 區塊 4：偵測 GPU 上的程序
# ==================================================================
# 結果放在 GPU_PROC_LINES，每筆是 "pid|gpu_index|used_mem_MiB"（掃 /proc 時顯存未知，記為 "-"）。
# GPU_DETECT_METHOD 記錄這次用哪種方式偵測到的，顯示在畫面上方便判斷結果可不可信。
detect_gpu_procs() {
    GPU_PROC_LINES=()
    GPU_DETECT_METHOD=""
    local idx uuid pid mem state

    # 方式 1：nvidia-smi。用 gpu_uuid 對回 GPU 編號（compute-apps 不直接給 index）。
    if command -v nvidia-smi >/dev/null 2>&1; then
        local -A uuid2idx=()
        while IFS='|' read -r idx uuid; do
            [ -n "$idx" ] && [ -n "$uuid" ] && uuid2idx[$uuid]=$idx
        done < <(nvidia-smi --query-gpu=index,uuid --format=csv,noheader 2>/dev/null | sed -E 's/, */|/g')

        while IFS='|' read -r pid uuid mem; do
            [[ "$pid" =~ ^[0-9]+$ ]] || continue
            [ -d "/proc/$pid" ] || continue
            # 容器內 nvidia-smi 列的是主機 PID，可能剛好撞到容器內另一個不相干的程序；
            # 看得到 fd 卻沒開 /dev/nvidiaN 的就是這種撞號，排除掉。看不到 fd（權限不足）則先相信。
            nvidia_fd_state "$pid"; state=$?
            [ "$state" -eq 1 ] && continue
            if [ -n "$uuid" ]; then idx="${uuid2idx[$uuid]:-?}"; else idx="?"; fi
            GPU_PROC_LINES+=("$pid|$idx|$mem")
        done < <(nvidia-smi --query-compute-apps=pid,gpu_uuid,used_memory --format=csv,noheader,nounits 2>/dev/null | sed -E 's/, */|/g')

        if [ ${#GPU_PROC_LINES[@]} -gt 0 ]; then
            GPU_DETECT_METHOD="nvidia-smi"
            return
        fi
    fi

    # 方式 2：掃 /proc，找開著 /dev/nvidiaN 的程序。一次 find 掃完所有程序的 fd，
    # 不要對每個 fd 各呼叫一次 readlink（訓練程序動輒幾百個 fd，會非常慢）。
    # 注意：/dev/nvidiaN 的 N 是裝置 minor number，通常等於 nvidia-smi 的 GPU 編號，但不保證。
    local dir target
    while read -r dir target; do
        pid="${dir#/proc/}"; pid="${pid%/fd}"
        [ "$pid" = "$$" ] && continue
        GPU_PROC_LINES+=("$pid|${target#/dev/nvidia}|-")
    done < <(find /proc/[0-9]*/fd -maxdepth 1 -lname '/dev/nvidia[0-9]*' -printf '%h %l\n' 2>/dev/null | sort -u)
    GPU_DETECT_METHOD="掃描 /proc（nvidia-smi 無可用的 PID）"
}

# ==================================================================
# 區塊 5：把 GPU 程序合併成「訓練工作」
# ==================================================================
# 規則：
#   1. 祖先程序也在 GPU 名單裡的，是它 fork 出來的子程序（例如 DataLoader worker，fork 時
#      繼承了 /dev/nvidiaN 的 fd），不另外列。
#   2. 帶有 RANK / LOCAL_RANK 環境變數的是分散式訓練的 rank，同一個父程序（torchrun /
#      accelerate / deepspeed launcher）底下的合併成一個工作。
#   3. 其餘程序各自是一個工作。
# 主 PID：RANK=0 的那個（沒有就找 LOCAL_RANK=0，再沒有就取最小的 PID），只有它會印進度條。
# 結果：JOB_KEYS（顯示順序）、JOB_PIDS[key]（空白分隔的 PID）、JOB_MAIN[key]（主 PID）。
build_jobs() {
    JOB_KEYS=()
    JOB_PIDS=()
    JOB_MAIN=()
    local -A is_gpu=()
    local line pid anc depth helper key rank lrank p

    for line in "${GPU_PROC_LINES[@]}"; do
        is_gpu[${line%%|*}]=1
    done

    for pid in $(printf '%s\n' "${!is_gpu[@]}" | sort -n); do
        helper=0
        anc=$(ppid_of "$pid"); depth=0
        while [ -n "$anc" ] && [ "$anc" -gt 1 ] && [ "$depth" -lt 10 ]; do
            if [ -n "${is_gpu[$anc]:-}" ]; then helper=1; break; fi
            anc=$(ppid_of "$anc"); depth=$((depth + 1))
        done
        [ "$helper" -eq 1 ] && continue

        if [ -n "$(env_value "$pid" RANK)$(env_value "$pid" LOCAL_RANK)" ]; then
            key="L$(ppid_of "$pid")"
        else
            key="P$pid"
        fi
        if [ -z "${JOB_PIDS[$key]:-}" ]; then
            JOB_KEYS+=("$key")
            JOB_PIDS[$key]="$pid"
        else
            JOB_PIDS[$key]+=" $pid"
        fi
    done

    for key in "${JOB_KEYS[@]}"; do
        JOB_MAIN[$key]=""
        for p in ${JOB_PIDS[$key]}; do
            rank=$(env_value "$p" RANK)
            if [ "$rank" = "0" ]; then JOB_MAIN[$key]="$p"; break; fi
        done
        if [ -z "${JOB_MAIN[$key]}" ]; then
            for p in ${JOB_PIDS[$key]}; do
                lrank=$(env_value "$p" LOCAL_RANK)
                if [ "$lrank" = "0" ]; then JOB_MAIN[$key]="$p"; break; fi
            done
        fi
        # JOB_PIDS 本來就是依 PID 由小到大加入的，第一個就是最小的
        [ -z "${JOB_MAIN[$key]}" ] && JOB_MAIN[$key]="${JOB_PIDS[$key]%% *}"
    done
}

# -p 指定 PID 時，只留下包含它的那個工作；它不在 GPU 名單裡（例如偵測不到）但程序存在時，
# 仍然把它當成單獨一個工作顯示，方便直接盯某個 PID。
filter_jobs_by_target() {
    local key
    for key in "${JOB_KEYS[@]}"; do
        if [[ " ${JOB_PIDS[$key]} " == *" $TARGET_PID "* ]]; then
            JOB_KEYS=("$key")
            return 0
        fi
    done
    JOB_KEYS=()
    [ -d "/proc/$TARGET_PID" ] || return 1
    key="P$TARGET_PID"
    JOB_KEYS=("$key")
    JOB_PIDS[$key]="$TARGET_PID"
    JOB_MAIN[$key]="$TARGET_PID"
}

# 某個工作用到的 GPU 與各卡顯存合計，例如 "GPU0 12345MiB  GPU1 12001MiB"
job_gpu_summary() {
    local pids=" $1 " line pid gpu mem out="" g
    local -A gpu_mem=()
    for line in "${GPU_PROC_LINES[@]}"; do
        IFS='|' read -r pid gpu mem <<< "$line"
        [[ "$pids" == *" $pid "* ]] || continue
        if [[ "$mem" =~ ^[0-9]+$ ]] && [[ "${gpu_mem[$gpu]:-0}" =~ ^[0-9]+$ ]]; then
            gpu_mem[$gpu]=$(( ${gpu_mem[$gpu]:-0} + mem ))
        else
            gpu_mem[$gpu]="-"
        fi
    done
    for g in $(printf '%s\n' "${!gpu_mem[@]}" | sort -n); do
        if [ "${gpu_mem[$g]}" = "-" ]; then
            out+="GPU$g  "
        else
            out+="GPU$g ${gpu_mem[$g]}MiB  "
        fi
    done
    echo "${out:-（未知）}"
}

# ==================================================================
# 區塊 6：解析訓練輸出（進度條、loss）
# ==================================================================
# 讀程序 stdout/stderr 的尾端。只讀指向「一般檔案」的 fd：指向終端機時 tail 會卡住等輸入，
# 指向 pipe 時會把資料從 pipe 搶走、讓真正的接收端少收資料，兩種都不能碰。
# 不管 tqdm 進度條寫到 stdout 還是 stderr（HF Trainer 預設寫 stderr）都看得到。
read_proc_output() {
    local pid="$1" fd f
    for fd in 1 2; do
        f="/proc/$pid/fd/$fd"
        if [ -f "$f" ] && [ -r "$f" ]; then
            tail -c "$TAIL_BYTES" "$f" 2>/dev/null
            echo
        fi
    done
}

# 從一段 tqdm 輸出裡挑出「訓練 step 進度條」那一行。HF Trainer 的訓練/評估進度條都沒有前綴
# 標籤（行首直接是百分比），但載入模型的 "Loading weights: 53%|..."、datasets 的 "Map: ..."
# 這類進度條有，先用「行首就是百分比」排除掉後者；剩下的訓練與 eval 進度條再用總 step 數區分：
# max_steps > 0 時訓練進度條的總數就是 max_steps，否則挑總數最大的那一行（eval 進度條的總數只是
# 驗證集 batch 數，遠小於整場訓練的 step 數）。
pick_train_progress_line() {
    local max_steps="$1"
    tr '\r' '\n' | grep -E '^[[:space:]]*[0-9]+%\|' | awk -v ms="$max_steps" '
        {
            if (match($0, /\| *[0-9]+\/[0-9]+/)) {
                s = substr($0, RSTART, RLENGTH); sub(/^\| */, "", s); split(s, a, "/"); total = a[2] + 0
            } else next
            if (ms + 0 > 0) { if (total == ms + 0) line = $0 }
            else if (total >= best) { best = total; line = $0 }
        }
        END { if (line != "") print line }'
}

# 印出進度條文字、藍色 step bar 與最新 loss。$1 = 輸出內容，$2 = max_steps（未知時 -1）。
# 回傳 0 = 有找到訓練進度條，1 = 沒找到（呼叫端依情況印不同的提示）。
show_progress_and_loss() {
    local output="$1" max_steps="$2" progress_line found=1
    # 去掉黏在進度條後面的 loss dict（下面另外顯示），只留進度條本身。
    progress_line=$(pick_train_progress_line "$max_steps" <<< "$output" | sed "s/{'.*//")
    if [ -n "$progress_line" ]; then
        found=0
        echo "進度：$progress_line"
        # 從 tqdm 文字（例如 "42%|████▏     | 210/500 [00:30<00:40, 7.25it/s]"）抓出
        # 「目前 step/總 step」算百分比，固定用藍色，跟依負載變色的 GPU bar 區分開來。
        if [[ "$progress_line" =~ ([0-9]+)/([0-9]+) ]]; then
            local step_cur="${BASH_REMATCH[1]}" step_total="${BASH_REMATCH[2]}" step_pct
            if [ "$step_total" -gt 0 ] 2>/dev/null; then
                step_pct=$(( step_cur * 100 / step_total ))
                printf "Step  ["
                draw_bar_fixed_color "$step_pct" 20 "$C_BLUE"
                printf "] %3s%%  (%s / %s step)\n" "$step_pct" "$step_cur" "$step_total"
            fi
        fi
    fi

    # HF Trainer 每 logging_steps / eval_steps 會印一行 Python dict（例如
    # {'loss': '1.234', 'grad_norm': ..., 'learning_rate': ..., 'epoch': '0.01'}），各取最後一行。
    # 這行 dict 常常緊接在 tqdm 進度條文字後面、沒有換行（實測："50%|█████| 5/10 [...]{'loss': ...}"），
    # 所以用 grep -o 從行中間抓出整段 dict，而不是要求它在行首。
    local last_train_log last_eval_log
    last_train_log=$(tr '\r' '\n' <<< "$output" | grep -oE "\{'loss'[^}]*\}" | tail -1)
    last_eval_log=$(tr '\r' '\n' <<< "$output" | grep -oE "\{'eval_loss'[^}]*\}" | tail -1)
    if [ -n "$last_train_log$last_eval_log" ]; then
        echo "train loss：${last_train_log:-（尚無紀錄）}"
        echo "eval  loss：${last_eval_log:-（尚無紀錄）}"
    fi
    return $found
}

# ==================================================================
# 區塊 7：單一工作的顯示區塊
# ==================================================================
# 找最新的 checkpoint：有 --output_dir 時只看那個資料夾底下一層；沒有時從程序的 cwd 往下
# 找 3 層（涵蓋常見的 Output/<run>/checkpoint-N）。cwd 底下若有大量檔案（例如資料集），
# 這個 find 會比較慢，遇到再改成帶 --output_dir 啟動訓練即可。
latest_checkpoint() {
    local dir="$1" depth="$2"
    find "$dir" -mindepth 1 -maxdepth "$depth" -type d -name 'checkpoint-*' -printf '%T@ %p\n' 2>/dev/null \
        | sort -n | tail -1 | cut -d' ' -f2-
}

show_job() {
    local key="$1" main pids n_ranks user etime pcpu pmem cmd
    main="${JOB_MAIN[$key]}"
    pids="${JOB_PIDS[$key]}"
    n_ranks=$(wc -w <<< "$pids")

    echo
    if [ "$n_ranks" -gt 1 ]; then
        echo "${C_BOLD}▶ PID $main（分散式訓練，$n_ranks 個 rank：$pids）${C_RESET}"
    else
        echo "${C_BOLD}▶ PID $main${C_RESET}"
    fi

    if ! [ -d "/proc/$main" ]; then
        echo "程序已結束"
        return
    fi
    read -r user etime pcpu pmem < <(ps -o user=,etime=,%cpu=,%mem= -p "$main" 2>/dev/null)
    printf "使用者：%s  執行時間：%s  CPU：%s%%  MEM：%s%%\n" "${user:--}" "${etime:--}" "${pcpu:--}" "${pmem:--}"
    echo "GPU：$(job_gpu_summary "$pids")"
    cmd=$(cmdline_str "$main")
    [ "${#cmd}" -gt "$CMD_MAX_LEN" ] && cmd="${cmd:0:$CMD_MAX_LEN}…"
    echo "CMD：$cmd"

    # 訓練參數：max_steps 用來從多條 tqdm 進度條裡挑出訓練那一條；output_dir 用來找 checkpoint。
    local max_steps output_dir cwd
    max_steps=$(cmdline_arg "$main" max_steps)
    [[ "$max_steps" =~ ^[0-9]+$ ]] || max_steps=-1
    cwd=$(readlink "/proc/$main/cwd" 2>/dev/null)
    output_dir=$(cmdline_arg "$main" output_dir)
    if [ -n "$output_dir" ] && [[ "$output_dir" != /* ]] && [ -n "$cwd" ]; then
        output_dir="$cwd/$output_dir"
    fi

    # 進度與 loss
    local output
    output=$(read_proc_output "$main")
    if [ -z "$output" ]; then
        echo "進度：（讀不到輸出——stdout/stderr 沒有導向檔案，或沒有權限讀 /proc/$main/fd）"
    elif ! show_progress_and_loss "$output" "$max_steps"; then
        echo "進度：（沒有偵測到訓練進度條——可能還在載入模型/資料，或不是訓練程序）"
    fi

    # checkpoint，並記下要檢查空間的磁碟路徑
    local ckpt
    if [ -n "$output_dir" ]; then
        ckpt=$(latest_checkpoint "$output_dir" 1)
        echo "checkpoint：${ckpt:-無（$output_dir 尚未存到第一個 checkpoint）}"
        DISK_PATHS+=("$output_dir")
    elif [ -n "$cwd" ]; then
        ckpt=$(latest_checkpoint "$cwd" 3)
        echo "checkpoint：${ckpt:-無（cwd $cwd 底下找不到 checkpoint-*）}"
        DISK_PATHS+=("$cwd")
    fi
}

# ==================================================================
# 區塊 8：系統資訊（CPU / 記憶體 / 磁碟 / GPU）
# ==================================================================
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

show_cpu_mem() {
    echo
    echo "--- CPU / 系統記憶體 ---"
    local width=20 cpu_pct mem_total mem_used mem_pct mem_total_gib mem_used_gib
    cpu_pct=$(cpu_usage_pct)
    printf "CPU  ["
    draw_bar "$cpu_pct" "$width"
    printf "] %3s%%\n" "$cpu_pct"

    read -r mem_total mem_used < <(free -b | awk '/^Mem:/{print $2, $3}')
    mem_pct=0
    if [ "${mem_total:-0}" -gt 0 ] 2>/dev/null; then
        mem_pct=$(( mem_used * 100 / mem_total ))
    fi
    mem_total_gib=$(awk -v b="${mem_total:-0}" 'BEGIN{printf "%.1f", b/1024/1024/1024}')
    mem_used_gib=$(awk -v b="${mem_used:-0}" 'BEGIN{printf "%.1f", b/1024/1024/1024}')
    printf "MEM  ["
    draw_bar "$mem_pct" "$width"
    printf "] %3s%%  (%sGi / %sGi)\n" "$mem_pct" "$mem_used_gib" "$mem_total_gib"
}

# 檢查的路徑：/mnt/moris（共用儲存，存在才看）＋各工作的 output_dir 或 cwd；
# 沒有任何工作時改看 /。同一個掛載點只顯示一次。
show_disk() {
    echo
    echo "--- 磁碟空間 ---"
    local -A seen_mount=()
    local paths=() p line size used pct mount
    [ -d /mnt/moris ] && paths+=("/mnt/moris")
    paths+=("${DISK_PATHS[@]}")
    [ ${#JOB_KEYS[@]} -eq 0 ] && paths+=("/")
    for p in "${paths[@]}"; do
        # -P 確保每個檔案系統只佔一行（裝置名稱太長時 df 預設會折行）
        line=$(df -P -h "$p" 2>/dev/null | tail -1)
        [ -z "$line" ] && continue
        read -r _ size used _ pct mount <<< "$line"
        [ -n "${seen_mount[$mount]:-}" ] && continue
        seen_mount[$mount]=1
        pct="${pct%\%}"
        printf "DISK ["
        draw_bar "${pct:-0}" 20
        printf "] %3s%%  (%s / %s)  掛載點：%s\n" "${pct:-0}" "$used" "$size" "$mount"
    done
}

show_gpu() {
    echo
    echo "--- GPU ---"
    if ! command -v nvidia-smi >/dev/null 2>&1; then
        echo "找不到 nvidia-smi"
        return
    fi
    local gpu_csv width=20 idx name util mem_used mem_total temp mem_pct
    # nounits 拿掉 "MiB"/"%" 等單位方便直接做數字運算；用 sed 把 ", " 換成 "|"
    # 而不是直接用逗號當分隔字元，這樣 GPU 名稱裡的空白（如 "NVIDIA GeForce RTX 4090"）
    # 才不會被拆成好幾個欄位。
    gpu_csv=$(nvidia-smi --query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu \
        --format=csv,noheader,nounits 2>/dev/null | sed -E 's/, */|/g')
    if [ -z "$gpu_csv" ]; then
        echo "讀不到 GPU 資訊（nvidia-smi 沒有輸出）"
        return
    fi
    while IFS='|' read -r idx name util mem_used mem_total temp; do
        [ -z "$idx" ] && continue
        mem_pct=0
        if [ "$mem_total" -gt 0 ] 2>/dev/null; then
            mem_pct=$(( mem_used * 100 / mem_total ))
        fi
        printf "GPU%s %-28s [" "$idx" "$name"
        draw_bar "$util" "$width"
        printf "] %3s%%  MEM[" "$util"
        draw_bar "$mem_pct" "$width"
        printf "] %6sMiB / %6sMiB  %s%s°C%s\n" \
            "$mem_used" "$mem_total" "$(temp_color "$temp")" "$temp" "$C_RESET"
    done <<< "$gpu_csv"
}

# ==================================================================
# 區塊 9：主流程
# ==================================================================
declare -a GPU_PROC_LINES=() JOB_KEYS=() DISK_PATHS=()
declare -A JOB_PIDS=() JOB_MAIN=()
GPU_DETECT_METHOD=""

snapshot() {
    DISK_PATHS=()
    echo "=================================================================="
    echo " 訓練監控快照 - $(date '+%Y-%m-%d %H:%M:%S')"
    echo "=================================================================="

    detect_gpu_procs
    build_jobs

    echo
    echo "--- GPU 程序（偵測方式：$GPU_DETECT_METHOD）---"
    if [ -n "$TARGET_PID" ] && ! filter_jobs_by_target; then
        echo "PID $TARGET_PID 不存在"
    elif [ ${#JOB_KEYS[@]} -eq 0 ]; then
        echo "狀態：⚠️ GPU 上沒有程序（訓練可能已完成、被中斷、或尚未啟動）"
        if [ -n "$LOG_FILE" ] && [ -f "$LOG_FILE" ]; then
            echo
            echo "改顯示 FINETUNE_LOG（$LOG_FILE）裡最後一次的紀錄："
            show_progress_and_loss "$(tail -c "$TAIL_BYTES" "$LOG_FILE" 2>/dev/null)" -1 \
                || echo "（日誌裡沒有訓練進度條紀錄）"
        fi
    else
        local key
        for key in "${JOB_KEYS[@]}"; do
            show_job "$key"
        done
    fi

    show_cpu_mem
    show_disk
    show_gpu
    echo "=================================================================="
}

if [ "$WATCH" -eq 1 ]; then
    while true; do
        # 先把整份快照算好再清畫面一次印出，避免收集資料那段時間畫面是空白的
        frame=$(snapshot)
        clear
        printf '%s\n' "$frame"
        sleep "$INTERVAL"
    done
else
    snapshot
fi
