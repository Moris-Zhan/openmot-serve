#!/usr/bin/env bash
# ============================================================
# Claude Code 常用 marketplace / plugin / skill / 工具 / MCP 安裝腳本（v18）
# 依 Notion「Claude Code Resources」Favorite 項目整理
#
# 用法：
#   ./favorites_install.v18.sh                     安裝全部「必裝」項目
#   ./favorites_install.v18.sh notion rtk          只安裝指定項目（必裝、選裝皆可）
#   ./favorites_install.v18.sh humanizer           單裝 plugin 時會自動先註冊其 marketplace
#   ./favorites_install.v18.sh --list              依類型列出所有項目
#
# 清除（解除安裝）：
#   ./favorites_install.v18.sh --uninstall [--dry-run] [-y] [--global] 項目名稱...
#   ./favorites_install.v18.sh --uninstall [--dry-run] [-y] [--global] --all
#     --dry-run  只列出會執行的指令，不實際清除，也不詢問確認
#     -y         不詢問確認
#     --global   skill 改清全域（預設只清專案；ai_copywriter 例外，一律清全域）
#     --all      清除清單中全部項目（不含 claude_plugins_official_marketplace）
#   只清除程式本身與全域設定，不刪除使用者產出的內容（openspec/、my-slide/、記憶資料等）
#
# 建議在「空專案根目錄」執行：skill 會裝到本專案的 .claude/skills/，
# 並一併完成 OpenSpec 初始化。
# ============================================================
 
# open-slide 建立的子資料夾名稱（可用環境變數覆蓋：SLIDE_DIR=deck ./favorites_install.v18.sh open_slide）
SLIDE_DIR="${SLIDE_DIR:-my-slide}"
 
# ============================================================
# 項目清單（讀這裡就能看完所有安裝項目）
# 不帶參數時依序執行：MARKETPLACES → PLUGINS → SKILLS → TOOLS → MCPS
# ============================================================
 
# ---------- 必裝 ----------
 
REQUIRED_MARKETPLACES=(
  planning_with_files_marketplace      # OthmanAdi/planning-with-files
  superpowers_marketplace              # obra/superpowers-marketplace
  ecc_marketplace                      # affaan-m/everything-claude-code
  anthropic_skills_marketplace         # anthropics/skills（anthropic-agent-skills）
  karpathy_skills_marketplace          # forrestchang/andrej-karpathy-skills
  claude_plugins_official_marketplace  # anthropics/claude-plugins-official（通常首次啟動會自動加入）
  knowledge_work_plugins_marketplace   # anthropics/knowledge-work-plugins
  playwright_skill_marketplace         # lackeyjb/playwright-skill
  claude_code_workflows_marketplace    # wshobson/agents（claude-code-workflows）
  i_have_adhd_marketplace              # ayghri/i-have-adhd
)
 
REQUIRED_PLUGINS=(                     # user 範圍（全域）
  planning_with_files      # (規劃實作) 以持久化檔案保存規劃狀態
  superpowers              # (程式開發) TDD、debugging、規劃與協作；內含 brainstorming / writing-plans / requesting-code-review
  ecc                      # (程式開發) everything-claude-code
  document_skills          # (ppt簡報 / 文件) docx、pdf、pptx、xlsx
  andrej_karpathy_skills   # (程式開發) Andrej Karpathy skills
  skill_creator            # (工具) 建立與調整 skill
  engineering              # (程式開發 / 軟體架構 / 程式碼審查) knowledge-work 工程 plugin
  design                   # (設計方案) knowledge-work 設計 plugin
  productivity             # (規劃實作 / 記憶管理) knowledge-work 任務與記憶 plugin
  playwright_skill         # (程式開發 / 測試) 瀏覽器自動化；裝完自動執行 npm run setup（Chromium）
  python_development       # (程式開發) Python 開發
  i_have_adhd              # (工作流程) i-have-adhd
)
 
REQUIRED_SKILLS=(                      # 全域或專案已有則略過，否則裝到本專案
  mattpocock_skills        # (程式開發) Matt Pocock 工程 skills 合集
  nature_paper2ppt         # (ppt簡報) 學術論文轉簡報
  ppt_visual               # (ppt簡報) 簡報視覺設計優化
  architecture_patterns    # (軟體架構) 軟體架構設計模式
  project_planner          # (規劃實作) 專案規劃
  open_code_review         # (程式碼審查) 開源程式碼審查
  memory_evolution         # (記憶管理) 記憶演化與更新機制；neural-memory 版，需 NeuralMemory
  memory_intake            # (記憶管理) 將資訊寫入記憶；neural-memory，需 NeuralMemory
  memory_audit             # (記憶管理) 檢查記憶品質；neural-memory，需 NeuralMemory
  code_simplification      # (程式碼審查) 在不改變行為的前提下簡化程式碼
  claude_automation_recommender  # (工具) Claude 自動化建議
  context_engineering      # (程式開發) 在對的時機提供對的 context
  book_to_skill            # (知識整理) 把書籍／文件轉成 Claude skill；PDF/EPUB 等格式需另裝擷取工具
  autoresearch             # (研究) 自主研究流程；依賴同 repo 其他 skill（ml-paper-writing、research ideation 等）
  prompt_master            # (提示工程) 為不同 AI 工具撰寫最佳化 prompt
)
 
REQUIRED_TOOLS=(
  openspec                 # (規劃實作) Spec-Driven Development：全域安裝 + 本專案 init
  graphify                 # (程式開發) 程式碼知識圖譜；需要 uv，寫入全域 ~/.claude/skills 與 ~/.claude/CLAUDE.md
  neural_memory            # (記憶管理) NeuralMemory 本體（Python 3.11+，pipx 優先）；memory_* skill 的依賴
)
 
REQUIRED_MCPS=(                        # user 範圍（全域）；已存在則略過
  context7                 # (程式開發) 最新函式庫文件查詢
  neural_memory_mcp        # (記憶管理) NeuralMemory MCP server（nmem-mcp）
)
 
# ---------- 選裝（需指定名稱） ----------
 
OPTIONAL_PLUGINS=(
  humanizer                # (寫作) 去除 AI 腔；會自動註冊 humanizer_marketplace
  product_management       # (產品規劃) knowledge-work 產品管理 plugin
  cowork_plugin_management # (工具) 建立／客製 plugin
  gbrain                   # (記憶管理 / 知識整理) GBrain 記憶與知識庫（MCP + skills）；會自動註冊 gbrain_marketplace；需另裝 gbrain CLI
)
 
OPTIONAL_SKILLS=(
  huggingface_vision_trainer     # HF 視覺模型訓練
  huggingface_llm_trainer        # HF LLM 訓練
  huggingface_papers             # HF 論文
  doc_coauthoring                # 文件協作撰寫
  ai_copywriter                  # (寫作) AI 文案撰寫；例外：裝到全域
  addy_agent_skills              # (程式開發) Addy Osmani 工程 skills 合集（整包 25 個）
)
 
OPTIONAL_TOOLS=(
  open_slide               # (ppt簡報) 簡報製作框架，於 ./$SLIDE_DIR 建立 workspace
  rtk                      # 壓縮 bash 輸出、降低 token（會改 ~/.bashrc 與全域 hook）
  gsd_core                 # (規劃實作 / 程式開發) GSD Core 規格驅動開發框架；官方安裝程式，裝到本專案（standard profile）
  gstack                   # (程式開發 / 工作流程 / 測試) Garry Tan 的 gstack；需要 bun、git；例外：裝到全域
)
 
OPTIONAL_MCPS=(
  notion                   # Notion（安裝後需在 Claude Code 執行 /mcp 完成授權）
)
 
# ---------- 附屬 marketplace（不在必裝或選裝清單；只供 --uninstall --all 使用） ----------
 
HIDDEN_MARKETPLACES=(
  humanizer_marketplace    # blader/humanizer
  gbrain_marketplace       # garrytan/gbrain
)
 
# ============================================================
# 各項目安裝 function
# 回傳值：0 = 成功；10 = 全域已存在；11 = 專案已存在；其他 = 失敗
# ============================================================
 
# ----- Marketplace -----
install_planning_with_files_marketplace()     { add_marketplace OthmanAdi/planning-with-files; }
install_superpowers_marketplace()             { add_marketplace obra/superpowers-marketplace; }
install_ecc_marketplace()                     { add_marketplace affaan-m/everything-claude-code; }
install_anthropic_skills_marketplace()        { add_marketplace anthropics/skills; }
install_karpathy_skills_marketplace()         { add_marketplace forrestchang/andrej-karpathy-skills; }
install_claude_plugins_official_marketplace() { add_marketplace anthropics/claude-plugins-official; }
install_humanizer_marketplace()               { add_marketplace blader/humanizer; }
install_knowledge_work_plugins_marketplace()  { add_marketplace anthropics/knowledge-work-plugins; }
install_playwright_skill_marketplace()        { add_marketplace lackeyjb/playwright-skill; }
install_claude_code_workflows_marketplace()   { add_marketplace wshobson/agents; }
install_i_have_adhd_marketplace()             { add_marketplace ayghri/i-have-adhd; }
install_gbrain_marketplace()                  { add_marketplace garrytan/gbrain; }
 
# ----- Plugin（第一個參數 = 所屬 marketplace 項目名稱）-----
install_planning_with_files()    { add_plugin planning_with_files_marketplace     planning-with-files@planning-with-files; }
install_superpowers()            { add_plugin superpowers_marketplace             superpowers@superpowers-marketplace; }
install_ecc()                    { add_plugin ecc_marketplace                     ecc@ecc; }
install_document_skills()        { add_plugin anthropic_skills_marketplace        document-skills@anthropic-agent-skills; }
install_andrej_karpathy_skills() { add_plugin karpathy_skills_marketplace         andrej-karpathy-skills@karpathy-skills; }
install_skill_creator()          { add_plugin claude_plugins_official_marketplace skill-creator@claude-plugins-official; }
install_humanizer()              { add_plugin humanizer_marketplace               humanizer@humanizer; }
install_engineering()              { add_plugin knowledge_work_plugins_marketplace engineering@knowledge-work-plugins; }
install_design()                   { add_plugin knowledge_work_plugins_marketplace design@knowledge-work-plugins; }
install_productivity()             { add_plugin knowledge_work_plugins_marketplace productivity@knowledge-work-plugins; }
install_product_management()       { add_plugin knowledge_work_plugins_marketplace product-management@knowledge-work-plugins; }
install_cowork_plugin_management() { add_plugin knowledge_work_plugins_marketplace cowork-plugin-management@knowledge-work-plugins; }
install_python_development()       { add_plugin claude_code_workflows_marketplace  python-development@claude-code-workflows; }
install_i_have_adhd()              { add_plugin i_have_adhd_marketplace            i-have-adhd@i-have-adhd; }
 
# gbrain：plugin 本身不含 gbrain 執行檔，需另裝 gbrain CLI 並建立 brain，MCP 才能啟動
#   例外：plugin 一律裝到全域（user 範圍），與其他 plugin 相同
install_gbrain() {
  add_plugin gbrain_marketplace gbrain@gbrain
  local rc=$?
  [ $rc -eq 0 ] && echo "  ⚠ 需先安裝 gbrain CLI：bun install -g github:garrytan/gbrain#latest-stable，再執行 gbrain init"
  return $rc
}
 
# playwright-skill：plugin 裝完後還需在 skill 資料夾執行 npm run setup（安裝 Playwright + Chromium）
#   路徑依官方 README；已有 node_modules/playwright 則略過 setup
#   要更新瀏覽器：手動到該資料夾執行 npm run setup；要 Firefox / WebKit：npm run install-all-browsers
PLAYWRIGHT_SKILL_DIR="$HOME/.claude/plugins/marketplaces/playwright-skill/skills/playwright-skill"
install_playwright_skill() {
  add_plugin playwright_skill_marketplace playwright-skill@playwright-skill || return 1
  if [ ! -d "$PLAYWRIGHT_SKILL_DIR" ]; then
    echo "  ✗ 找不到 skill 資料夾，無法執行 setup：$PLAYWRIGHT_SKILL_DIR"
    return 1
  fi
  if [ -d "$PLAYWRIGHT_SKILL_DIR/node_modules/playwright" ]; then
    echo "  Playwright 已安裝，略過 setup"
    return 0
  fi
  (cd "$PLAYWRIGHT_SKILL_DIR" && npm run setup)
}
 
# ----- Skill（第一個參數 = 用來判斷是否已安裝的 skill 名稱）-----
# mattpocock/skills 整個 repo 一次安裝；以其中的 grill-me 判斷是否已安裝
install_mattpocock_skills()     { add_skill grill-me              mattpocock/skills; }
install_nature_paper2ppt()      { add_skill nature-paper2ppt      https://github.com/yuan1z0825/nature-skills --skill nature-paper2ppt; }
install_ppt_visual()            { add_skill ppt-visual            https://github.com/claude-office-skills/skills --skill ppt-visual; }
install_architecture_patterns() { add_skill architecture-patterns wshobson/agents@architecture-patterns; }
install_project_planner()       { add_skill project-planner       https://github.com/modimihir07/agentic-os/tree/main/skills/project-planner; }
install_open_code_review()      { add_skill open-code-review      https://github.com/alibaba/open-code-review --skill open-code-review; }
# neural-memory 系列 skill 需要 NeuralMemory 本體（tool：neural_memory）與 MCP（neural_memory_mcp）
# 注意：判斷名稱沿用 memory-evolution；若舊版 memory-evolution 已存在，會被判定已安裝而略過，需手動移除舊資料夾後重跑
install_memory_evolution()      { add_skill memory-evolution      nhadaututtheky/neural-memory --skill memory-evolution; }
install_memory_intake()         { add_skill memory-intake         nhadaututtheky/neural-memory --skill memory-intake; }
install_memory_audit()          { add_skill memory-audit          nhadaututtheky/neural-memory --skill memory-audit; }
install_code_simplification()   { add_skill code-simplification   https://github.com/addyosmani/agent-skills --skill code-simplification; }
install_context_engineering()   { add_skill context-engineering   addyosmani/agent-skills@context-engineering; }
# 依賴檢查：到 skill 資料夾執行 python3 scripts/extract.py --check，會列出缺少的擷取工具
install_book_to_skill()         { add_skill book-to-skill         virgiliojr94/book-to-skill; }
# autoresearch 會呼叫同 repo 的其他 skill；文獻搜尋需 pip 套件 semanticscholar、arxiv（未寫進 script）
install_autoresearch()          { add_skill autoresearch          Orchestra-Research/AI-research-SKILLs@autoresearch; }
# prompt-master 的實際資料夾名稱未查證；若不同，只會造成每次重跑都重裝
install_prompt_master()         { add_skill prompt-master         nidhinjs/prompt-master; }
 
install_huggingface_vision_trainer()    { add_skill huggingface-vision-trainer    https://github.com/huggingface/skills --skill huggingface-vision-trainer; }
install_huggingface_llm_trainer()       { add_skill huggingface-llm-trainer       https://github.com/huggingface/skills --skill huggingface-llm-trainer; }
install_huggingface_papers()            { add_skill huggingface-papers            https://github.com/huggingface/skills --skill huggingface-papers; }
install_claude_automation_recommender() { add_skill claude-automation-recommender https://github.com/anthropics/claude-plugins-official --skill claude-automation-recommender; }
install_doc_coauthoring()               { add_skill doc-coauthoring               anthropics/skills@doc-coauthoring; }
# 例外：此項裝到全域（--global），與專案無關、所有專案共用
install_ai_copywriter()                 { add_skill ai-copywriter                 mikiarlo3/ai-copywriter --global; }
# addyosmani/agent-skills 整個 repo 一次安裝；以其中的 using-agent-skills 判斷是否已安裝
# 注意：整包含 code-simplification、context-engineering，與必裝的 code_simplification、context_engineering 重疊
#   npx skills add 遇到已存在的 skill 資料夾會覆蓋還是略過，未查證
install_addy_agent_skills()             { add_skill using-agent-skills            addyosmani/agent-skills; }
 
# ----- Tool -----
install_openspec() {
  npm install -g @fission-ai/openspec@latest || return 1
  # 更新用：npm install -g @fission-ai/openspec@latest && openspec update
  if [ -d openspec ]; then
    echo "  本專案已有 openspec/，略過 init"
    return 11
  fi
  openspec init --tools claude
}
 
# graphify: how the knowledge graph is built (run from the project root, after install)
#   Pipeline: detect files -> AST for code (tree-sitter, no LLM, no API key)
#             -> semantic extraction for docs/papers/images (host agent, or Gemini if GEMINI_API_KEY is set)
#             -> merge -> build graph -> community clustering -> label -> export
#   Every edge is tagged EXTRACTED (explicit in source), INFERRED (with a score) or AMBIGUOUS.
#   Outputs go to ./graphify-out/: graph.json, graph.html, GRAPH_REPORT.md (+ manifest.json and cache/ for --update)
#
#   Build (inside Claude Code):
#     /graphify .                  full pipeline on the current project
#     /graphify . --update         re-extract only new or changed files
#     /graphify . --mode deep      richer INFERRED edges
#   Build (headless, CI or terminal):
#     graphify extract . --code-only        code only, local AST, no API key
#     graphify extract . --backend gemini   AST + semantic extraction (backend needs its API key)
#     graphify update .                     code-only incremental rebuild, no LLM
#   Query:
#     graphify query "<question>" | graphify path "A" "B" | graphify explain "X" | graphify god-nodes
#   Export:
#     graphify export html | obsidian | wiki | svg | graphml | neo4j
#   Exclusions: list patterns (gitignore syntax) in ./.graphifyignore, otherwise installed skills and
#     tool scripts swamp the graph. Example for this repo: .claude/  .agents/  skills-lock.json  scripts/
#   graphify-out/ lands in the working tree: add it to .gitignore, or commit only graph.json and GRAPH_REPORT.md
install_graphify() {
  # 套件名稱是 graphifyy（兩個 y），指令是 graphify
  # graphify install 會寫入全域：~/.claude/skills/graphify/SKILL.md、~/.claude/CLAUDE.md（新版另加 PreToolUse hook）
  if ! command -v graphify >/dev/null 2>&1; then
    if ! command -v uv >/dev/null 2>&1; then
      echo "  ✗ 找不到 uv，請先安裝：curl -LsSf https://astral.sh/uv/install.sh | sh"
      return 1
    fi
    uv tool install graphifyy || return 1
    # uv 預設把指令裝到 ~/.local/bin，若不在 PATH 就找不到 graphify
    export PATH="$(uv tool dir --bin 2>/dev/null || echo "$HOME/.local/bin"):$PATH"
    if ! command -v graphify >/dev/null 2>&1; then
      echo "  ✗ 已安裝 graphifyy，但找不到 graphify 指令；請執行 uv tool update-shell 後重開終端機"
      return 1
    fi
  else
    echo "  已有 graphify 指令，略過 uv tool install"
  fi
  if [ -f "$HOME/.claude/skills/graphify/SKILL.md" ]; then
    echo "  全域已註冊 graphify skill，略過 graphify install"
    return 10
  fi
  graphify install
}
 
install_neural_memory() {
  # PyPI 套件 neural-memory，提供 nmem-mcp 指令；首次使用自動初始化
  if command -v nmem-mcp >/dev/null 2>&1; then
    echo "  已有 nmem-mcp 指令，略過"
    return 10
  fi
  if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    echo "  ✗ 需要 Python 3.11 以上（目前：$(python3 --version 2>&1)）"
    return 1
  fi
  if command -v pipx >/dev/null 2>&1; then
    pipx install neural-memory || return 1
  else
    python3 -m pip install --user neural-memory || {
      echo "  ✗ pip 安裝失敗；若系統 Python 禁止安裝（PEP 668），請先安裝 pipx 後重跑"
      return 1
    }
  fi
  export PATH="$HOME/.local/bin:$PATH"
  if ! command -v nmem-mcp >/dev/null 2>&1; then
    echo "  ✗ 已安裝 neural-memory，但找不到 nmem-mcp 指令；請確認 ~/.local/bin 在 PATH 中"
    return 1
  fi
}
 
install_open_slide() {
  # 以 /create-slide、/slide-authoring、/apply-comments 驅動簡報製作循環
  if [ -e "$SLIDE_DIR" ]; then
    echo "  ./$SLIDE_DIR 已存在，略過"
    return 11
  fi
  npx --yes @open-slide/cli init "$SLIDE_DIR"
}
 
install_rtk() {
  # 注意：以 curl | sh 執行遠端安裝腳本
  curl -fsSL https://raw.githubusercontent.com/rtk-ai/rtk/refs/heads/master/install.sh | sh || return 1
  export PATH="$HOME/.local/bin:$PATH"
  grep -qxF 'export PATH="$HOME/.local/bin:$PATH"' ~/.bashrc 2>/dev/null || \
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
  rtk --version            || return 1
  rtk gain
  rtk init -g --dry-run    # 預覽全域 hook 變更
  rtk init -g --hook-only  || return 1
  rtk init --show
  # 確認沒有設定錯誤的 API 位址
  grep -n 'ANTHROPIC_BASE_URL' ~/.claude/settings.json || true
}
 
install_gsd_core() {
  # 官方安裝程式（npm 套件 @opengsd/gsd-core），會裝入 skills、commands、hooks、agents；重跑即更新
  # 判定路徑未查證：假設 --local 會建立 .claude/skills/gsd-help/；若不符，只會造成每次重跑都重裝
  if [ -d ".claude/skills/gsd-help" ]; then
    echo "  專案已有 gsd-help，略過"
    return 11
  fi
  npx --yes @opengsd/gsd-core@latest --claude --local --profile=standard
}
 
# gstack：git clone 後執行 ./setup（需要 bun；setup 會下載 Playwright Chromium，並建立 ~/.gstack）
#   例外：裝到全域。官方另要求在 CLAUDE.md 加一段 gstack 說明，本腳本不自動寫入
#   注意：若上次 setup 中途失敗，資料夾已存在會被判定已安裝而略過，需手動刪除該資料夾後重跑
GSTACK_DIR="$HOME/.claude/skills/gstack"
install_gstack() {
  if [ -d "$GSTACK_DIR" ]; then
    echo "  全域已有 gstack，略過"
    return 10
  fi
  if ! command -v git >/dev/null 2>&1; then
    echo "  ✗ 找不到 git，請先安裝 git"
    return 1
  fi
  if ! command -v bun >/dev/null 2>&1; then
    echo "  ✗ 找不到 bun，請先安裝：curl -fsSL https://bun.sh/install | bash（裝完重開終端機）"
    return 1
  fi
  git clone --single-branch --depth 1 https://github.com/garrytan/gstack.git "$GSTACK_DIR" || return 1
  (cd "$GSTACK_DIR" && ./setup) || return 1
  echo "  ⚠ 未自動寫入 CLAUDE.md；如需官方建議的 gstack 說明段落，請參考 gstack README 手動加入"
}
 
# ----- MCP（第一個參數 = server 名稱，其餘接在 claude mcp add --scope user 之後）-----
install_context7() { add_mcp context7 context7 -- npx -y @upstash/context7-mcp; }
# 需先有 nmem-mcp 指令（tool：neural_memory）；必裝順序中 tool 會先於 MCP 執行
install_neural_memory_mcp() { add_mcp neural-memory neural-memory -- nmem-mcp; }
 
install_notion() {
  add_mcp notion --transport http notion https://mcp.notion.com/mcp
  local rc=$?
  [ $rc -eq 0 ] && echo "  ⚠ 請在 Claude Code 執行 /mcp 完成 Notion 授權"
  return $rc
}
 
# ============================================================
# 各項目清除 function（--uninstall）
# 回傳值：0 = 已清除；20 = 未安裝，略過；12 = 此平台不支援；其他 = 失敗
# 原則：只清程式本身與全域設定；使用者產出的內容與外部依賴一律保留，只印提示
# ============================================================
 
# ----- Marketplace（第一個參數 = marketplace 名稱，其餘 = 其底下由本腳本登記的 plugin 項目）-----
# marketplace 名稱取自 plugin@marketplace 的後半段（未查證）
uninstall_planning_with_files_marketplace()     { remove_marketplace planning-with-files     planning_with_files; }
uninstall_superpowers_marketplace()             { remove_marketplace superpowers-marketplace superpowers; }
uninstall_ecc_marketplace()                     { remove_marketplace ecc                     ecc; }
uninstall_anthropic_skills_marketplace()        { remove_marketplace anthropic-agent-skills  document_skills; }
uninstall_karpathy_skills_marketplace()         { remove_marketplace karpathy-skills         andrej_karpathy_skills; }
uninstall_claude_plugins_official_marketplace() { remove_marketplace claude-plugins-official skill_creator; }
uninstall_humanizer_marketplace()               { remove_marketplace humanizer               humanizer; }
uninstall_knowledge_work_plugins_marketplace()  { remove_marketplace knowledge-work-plugins  engineering design productivity product_management cowork_plugin_management; }
uninstall_playwright_skill_marketplace()        { remove_marketplace playwright-skill        playwright_skill; }
uninstall_claude_code_workflows_marketplace()   { remove_marketplace claude-code-workflows   python_development; }
uninstall_i_have_adhd_marketplace()             { remove_marketplace i-have-adhd             i_have_adhd; }
uninstall_gbrain_marketplace()                  { remove_marketplace gbrain                  gbrain; }
 
# ----- Plugin（不會連帶清除所屬 marketplace）-----
uninstall_planning_with_files()      { remove_plugin planning-with-files@planning-with-files; }
uninstall_superpowers()              { remove_plugin superpowers@superpowers-marketplace; }
uninstall_ecc()                      { remove_plugin ecc@ecc; }
uninstall_document_skills()          { remove_plugin document-skills@anthropic-agent-skills; }
uninstall_andrej_karpathy_skills()   { remove_plugin andrej-karpathy-skills@karpathy-skills; }
uninstall_skill_creator()            { remove_plugin skill-creator@claude-plugins-official; }
uninstall_humanizer()                { remove_plugin humanizer@humanizer; }
uninstall_engineering()              { remove_plugin engineering@knowledge-work-plugins; }
uninstall_design()                   { remove_plugin design@knowledge-work-plugins; }
uninstall_productivity()             { remove_plugin productivity@knowledge-work-plugins; }
uninstall_product_management()       { remove_plugin product-management@knowledge-work-plugins; }
uninstall_cowork_plugin_management() { remove_plugin cowork-plugin-management@knowledge-work-plugins; }
uninstall_python_development()       { remove_plugin python-development@claude-code-workflows; }
uninstall_i_have_adhd()              { remove_plugin i-have-adhd@i-have-adhd; }
 
uninstall_gbrain() {
  remove_plugin gbrain@gbrain
  local rc=$?
  [ $rc -eq 0 ] && echo "  保留 gbrain CLI、bun 與 brain 資料（不是本腳本安裝的）；需要的話請手動處理"
  return $rc
}
 
uninstall_playwright_skill() {
  remove_plugin playwright-skill@playwright-skill
  local rc=$?
  [ $rc -eq 0 ] && echo "  保留 Playwright 下載的瀏覽器（~/.cache/ms-playwright，其他專案可能也在用）；需要的話請手動刪除"
  return $rc
}
 
# ----- Skill（預設清專案，加 --global 改清全域）-----
# 合集：執行時讀取 repo 的 skill 清單，只清除清單中、且實際存在於該範圍的 skill
uninstall_mattpocock_skills()     { remove_skill_collection mattpocock/skills grill-me; }
uninstall_nature_paper2ppt()      { remove_skill nature-paper2ppt; }
uninstall_ppt_visual()            { remove_skill ppt-visual; }
uninstall_architecture_patterns() { remove_skill architecture-patterns; }
uninstall_project_planner()       { remove_skill project-planner; }
uninstall_open_code_review()      { remove_skill open-code-review; }
uninstall_memory_evolution()      { remove_skill memory-evolution; }
uninstall_memory_intake()         { remove_skill memory-intake; }
uninstall_memory_audit()          { remove_skill memory-audit; }
uninstall_code_simplification()   { remove_skill code-simplification; }
uninstall_context_engineering()   { remove_skill context-engineering; }
uninstall_book_to_skill()         { remove_skill book-to-skill; }
uninstall_autoresearch()          { remove_skill autoresearch; }
uninstall_prompt_master()         { remove_skill prompt-master; }
 
uninstall_huggingface_vision_trainer()    { remove_skill huggingface-vision-trainer; }
uninstall_huggingface_llm_trainer()       { remove_skill huggingface-llm-trainer; }
uninstall_huggingface_papers()            { remove_skill huggingface-papers; }
uninstall_claude_automation_recommender() { remove_skill claude-automation-recommender; }
uninstall_doc_coauthoring()               { remove_skill doc-coauthoring; }
# 例外：安裝時一律裝到全域，所以一律清全域（不看 --global）
uninstall_ai_copywriter()                 { remove_skill_in global ai-copywriter; }
# 排除 code-simplification、context-engineering：它們也是必裝項目，除非本次也指定清除那兩個項目
uninstall_addy_agent_skills()             { remove_skill_collection addyosmani/agent-skills using-agent-skills code-simplification:code_simplification context-engineering:context_engineering; }
 
# ----- Tool -----
uninstall_openspec() {
  local rc=0
  if npm ls -g --depth=0 @fission-ai/openspec >/dev/null 2>&1; then
    run_cmd npm uninstall -g @fission-ai/openspec || return 1
  else
    echo "  未安裝全域 openspec，略過"
    rc=20
  fi
  [ -d openspec ] && echo "  保留本專案的 openspec/ 與 .claude 內由 openspec init 產生的檔案；需要的話請手動刪除"
  return $rc
}
 
# graphify uninstall 會從所有偵測到的 AI 工具移除 graphify skill；不加 --purge，圖譜資料保留
uninstall_graphify() {
  local did=0
  if command -v graphify >/dev/null 2>&1; then
    run_cmd graphify uninstall || return 1
    did=1
  elif [ -f "$HOME/.claude/skills/graphify/SKILL.md" ]; then
    echo "  ✗ 全域仍有 graphify skill，但找不到 graphify 指令，無法執行 graphify uninstall"
    echo "    請手動刪除 ~/.claude/skills/graphify，並檢查 ~/.claude/CLAUDE.md 與 settings.json 的 hook"
    return 1
  fi
  # uv tool list 的輸出格式未查證：假設每個工具一行，以套件名稱開頭
  if command -v uv >/dev/null 2>&1 && uv tool list 2>/dev/null | grep -q '^graphifyy '; then
    run_cmd uv tool uninstall graphifyy || return 1
    did=1
  fi
  if [ $did -eq 0 ]; then
    echo "  未安裝 graphify，略過"
    return 20
  fi
  echo "  保留各專案的 graphify-out/ 圖譜資料；需要的話請手動刪除"
}
 
uninstall_neural_memory() {
  if command -v pipx >/dev/null 2>&1 && pipx list --short 2>/dev/null | grep -q '^neural-memory '; then
    run_cmd pipx uninstall neural-memory || return 1
  elif python3 -m pip show neural-memory >/dev/null 2>&1; then
    run_cmd python3 -m pip uninstall -y neural-memory || return 1
  else
    echo "  未安裝 neural-memory，略過"
    return 20
  fi
  warn_dependents neural_memory memory_evolution memory_intake memory_audit neural_memory_mcp
  echo "  保留 NeuralMemory 的記憶資料（存放位置未查證）；需要的話請手動刪除"
}
 
uninstall_open_slide() {
  echo "  open_slide 沒有可清除的程式或全域設定"
  [ -e "$SLIDE_DIR" ] && echo "  保留 ./$SLIDE_DIR（簡報內容）；需要的話請手動刪除"
  return 20
}
 
# rtk：先用 rtk 自己的 uninstall 移除全域 hook 與 RTK.md，再刪除 ~/.local/bin/rtk
uninstall_rtk() {
  local bin
  bin=$(command -v rtk 2>/dev/null) || bin=""
  [ -z "$bin" ] && [ -x "$HOME/.local/bin/rtk" ] && bin="$HOME/.local/bin/rtk"
  if [ -z "$bin" ]; then
    echo "  未安裝 rtk，略過"
    return 20
  fi
  run_cmd "$bin" init -g --uninstall || return 1
  if [ "$bin" = "$HOME/.local/bin/rtk" ]; then
    run_cmd rm -f "$bin" || return 1
  else
    echo "  ⚠ rtk 不在 ~/.local/bin（實際位置：$bin），不是本腳本安裝的位置，未刪除執行檔"
  fi
  echo "  保留 ~/.bashrc 的 PATH 設定（其他工具可能也在用）與 ~/.local/share/rtk 追蹤資料；需要的話請手動刪除"
}
 
# gsd_core：查不到 @opengsd/gsd-core 已查證的清除方式；不猜旗標，避免誤觸重新安裝
uninstall_gsd_core() {
  if [ ! -d ".claude/skills/gsd-help" ]; then
    echo "  專案沒有 gsd-help（判定路徑未查證），視為未安裝"
    return 20
  fi
  echo "  ✗ gsd_core 尚無已查證的清除方式，請手動清除"
  echo "    可先執行 npx @opengsd/gsd-core@latest --help，確認有沒有 uninstall 選項"
  return 1
}
 
uninstall_gstack() {
  if [ ! -d "$GSTACK_DIR" ]; then
    echo "  全域沒有 gstack，略過"
    return 20
  fi
  run_cmd rm -rf "$GSTACK_DIR" || return 1
  echo "  保留 ~/.gstack 與 Playwright 下載的瀏覽器；需要的話請手動刪除"
}
 
# ----- MCP -----
uninstall_context7() { remove_mcp context7; }
 
uninstall_neural_memory_mcp() {
  remove_mcp neural-memory
  local rc=$?
  [ $rc -eq 0 ] && warn_dependents neural_memory_mcp memory_evolution memory_intake memory_audit
  return $rc
}
 
uninstall_notion() { remove_mcp notion; }
 
# ============================================================
# 共用 helper（一般不需要看）
# ============================================================
 
# 本次執行已處理過的 marketplace（相容 macOS 內建 bash 3.2，不用關聯陣列）
DONE_MARKETPLACES=" "
 
# add_marketplace <marketplace-repo>
add_marketplace() {
  claude plugin marketplace add "$1"
}
 
# add_plugin <marketplace 項目名稱> <plugin@marketplace>
add_plugin() {
  local mp="$1" plugin="$2"
  case "$DONE_MARKETPLACES" in
    *" $mp "*) ;;
    *) echo "  先註冊所屬 marketplace：$mp"
       run_item "$mp" ;;
  esac
  claude plugin install "$plugin" -y
}
 
# add_skill <檢查用 skill 名稱> <來源> [其他 npx skills 參數...]
#   全域 ~/.claude/skills/<名稱>/SKILL.md 存在 → 略過（10）
#   專案 .claude/skills/<名稱>/ 存在           → 略過（11）
#   否則裝到本專案
add_skill() {
  local name="$1"; shift
  if [ -f "$HOME/.claude/skills/$name/SKILL.md" ]; then
    echo "  全域已有 $name，略過"
    return 10
  fi
  if [ -d ".claude/skills/$name" ]; then
    echo "  專案已有 $name，略過"
    return 11
  fi
  npx --yes skills@latest add "$@" --agent claude-code -y
}
 
# add_mcp <server 名稱> <claude mcp add 的其餘參數...>
#   claude mcp get 找得到 → 略過（10）
#   注意：假設 claude mcp get 找不到時回傳非 0（未經文件證實）
add_mcp() {
  local name="$1"; shift
  if claude mcp get "$name" >/dev/null 2>&1; then
    echo "  已有 MCP $name，略過"
    return 10
  fi
  claude mcp add --scope user "$@"
}
 
OK_LIST=(); SKIP_GLOBAL_LIST=(); SKIP_PROJECT_LIST=(); FAIL_LIST=()
 
run_item() {
  local item="${1//-/_}"   # 允許 ppt-visual 這種寫法
  local fn="install_$item"
  if ! declare -F "$fn" >/dev/null; then
    echo "✗ 未知項目：$1"
    FAIL_LIST+=("$1（未知項目）")
    return
  fi
  # marketplace 同一次執行只處理一次
  case "$item" in
    *_marketplace)
      case "$DONE_MARKETPLACES" in *" $item "*) return ;; esac
      DONE_MARKETPLACES="$DONE_MARKETPLACES$item "
      ;;
  esac
  echo "▶ $item"
  "$fn"
  case $? in
    0)  OK_LIST+=("$item") ;;
    10) SKIP_GLOBAL_LIST+=("$item") ;;
    11) SKIP_PROJECT_LIST+=("$item") ;;
    *)  FAIL_LIST+=("$item") ;;
  esac
}
 
print_group() {
  local title="$1"; shift
  echo "  [$title]"
  [ $# -gt 0 ] && printf '    %s\n' "$@"
}
 
print_list() {
  echo "必裝（不帶參數時依此順序執行）："
  print_group marketplace "${REQUIRED_MARKETPLACES[@]}"
  print_group plugin      "${REQUIRED_PLUGINS[@]}"
  print_group skill       "${REQUIRED_SKILLS[@]}"
  print_group tool        "${REQUIRED_TOOLS[@]}"
  print_group mcp         "${REQUIRED_MCPS[@]}"
  echo "選裝（需指定名稱）："
  print_group plugin      "${OPTIONAL_PLUGINS[@]}"
  print_group skill       "${OPTIONAL_SKILLS[@]}"
  print_group tool        "${OPTIONAL_TOOLS[@]}"
  print_group mcp         "${OPTIONAL_MCPS[@]}"
}
 
print_summary() {
  echo
  echo "================ 安裝摘要 ================"
  echo "成功（${#OK_LIST[@]}）：${OK_LIST[*]}"
  echo "已存在-全域（${#SKIP_GLOBAL_LIST[@]}）：${SKIP_GLOBAL_LIST[*]}"
  echo "已存在-專案（${#SKIP_PROJECT_LIST[@]}）：${SKIP_PROJECT_LIST[*]}"
  echo "失敗（${#FAIL_LIST[@]}）：${FAIL_LIST[*]}"
  [ ${#FAIL_LIST[@]} -eq 0 ]
}
 
print_usage() {
  local me
  me=$(basename "$0")
  echo "用法："
  echo "  $me                       安裝全部「必裝」項目"
  echo "  $me 項目名稱...            只安裝指定項目"
  echo "  $me --list                依類型列出所有項目"
  echo "  $me --uninstall [--dry-run] [-y] [--global] 項目名稱..."
  echo "  $me --uninstall [--dry-run] [-y] [--global] --all"
  echo "    --dry-run  只列出會執行的指令，不實際清除，也不詢問確認"
  echo "    -y         不詢問確認"
  echo "    --global   skill 改清全域（預設只清專案；ai_copywriter 例外，一律清全域）"
  echo "    --all      清除清單中全部項目（不含 claude_plugins_official_marketplace）"
  echo "  選項一律放在項目名稱之前"
}
 
# ---------- 清除模式 helper ----------
 
DRY_RUN=0          # --dry-run
ASSUME_YES=0       # -y
UN_SCOPE=project   # skill 的清除範圍：project 或 global（--global）
UN_TARGETS=" "     # 本次指定清除的項目（前後有空白，供比對）
UN_DONE=" "        # 本次已處理過的項目
UN_FAILED=" "      # 本次清除失敗的項目
CLEARED_LIST=(); NOT_INSTALLED_LIST=(); UNSUPPORTED_LIST=()
 
# run_cmd <指令...>：--dry-run 時只印出指令
run_cmd() {
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "  [dry-run] $*"
    return 0
  fi
  "$@"
}
 
# find_token <名稱>：標準輸入中有完整的「名稱」字樣時回傳 0
#   前後必須不是英數字、底線或連字號，避免 code-review 誤中 code-review-and-quality
find_token() {
  local re
  re=$(printf '%s' "$1" | sed 's/[][\.*^$()+?{}|/]/\\&/g')
  grep -Eq "(^|[^A-Za-z0-9_-])${re}([^A-Za-z0-9_-]|\$)"
}
 
# warn_dependents <被清除的項目> <依賴它的項目...>：只警告，不連帶清除
warn_dependents() {
  local self="$1" d left=()
  shift
  for d in "$@"; do
    case "$UN_TARGETS" in *" $d "*) ;; *) left+=("$d") ;; esac
  done
  [ ${#left[@]} -gt 0 ] && echo "  ⚠ 下列項目依賴 $self，若已安裝將無法運作（未一併清除）：${left[*]}"
  return 0
}
 
# remove_plugin <plugin@marketplace>
#   claude plugin list 的輸出沒有此 plugin → 未安裝（20）
#   注意：list 的純文字輸出格式未查證；list 執行失敗時略過偵測，直接清除
remove_plugin() {
  local id="$1" out
  if out=$(claude plugin list 2>/dev/null); then
    if ! printf '%s\n' "$out" | find_token "$id"; then
      echo "  未安裝 $id，略過"
      return 20
    fi
  else
    echo "  ⚠ 無法執行 claude plugin list，略過偵測，直接清除"
  fi
  run_cmd claude plugin uninstall "$id"
}
 
# remove_marketplace <marketplace 名稱> [plugin 項目...]
#   先清除其底下由本腳本登記的 plugin，避免留下孤兒紀錄；任一 plugin 清除失敗就不清 marketplace
remove_marketplace() {
  local name="$1" p out
  shift
  if [ $# -gt 0 ]; then
    echo "  先清除其底下的 plugin：$*"
    for p in "$@"; do un_run_item "$p"; done
    for p in "$@"; do
      case "$UN_FAILED" in
        *" $p "*)
          echo "  ✗ plugin $p 清除失敗，為避免留下孤兒紀錄，不清除 marketplace $name"
          return 1 ;;
      esac
    done
  fi
  if out=$(claude plugin marketplace list 2>/dev/null); then
    if ! printf '%s\n' "$out" | find_token "$name"; then
      echo "  未安裝 marketplace $name，略過"
      return 20
    fi
  else
    echo "  ⚠ 無法執行 claude plugin marketplace list，略過偵測，直接清除"
  fi
  run_cmd claude plugin marketplace remove "$name"
}
 
# skills_root <global|project>
skills_root() {
  if [ "$1" = global ]; then echo "$HOME/.claude/skills"; else echo ".claude/skills"; fi
}
 
# remove_skill_in <global|project> <skill 名稱...>
#   只清除該範圍實際存在的 skill；一個都沒有 → 未安裝（20），若另一個範圍有則提示
remove_skill_in() {
  local scope="$1" root n found=() gflag=()
  shift
  root=$(skills_root "$scope")
  for n in "$@"; do
    [ -d "$root/$n" ] && found+=("$n")
  done
  if [ ${#found[@]} -eq 0 ]; then
    if [ "$scope" = project ] && [ -d "$HOME/.claude/skills/$1" ]; then
      echo "  專案沒有 $1；全域仍有，加 --global 才會清"
    elif [ "$scope" = global ] && [ -d ".claude/skills/$1" ]; then
      echo "  全域沒有 $1；專案仍有，不加 --global 才會清"
    else
      echo "  未安裝 $*，略過"
    fi
    return 20
  fi
  [ "$scope" = global ] && gflag=(--global)
  run_cmd npx --yes skills@latest remove "${found[@]}" --agent claude-code -y "${gflag[@]}" || return 1
  [ "$DRY_RUN" -eq 1 ] && return 0
  for n in "${found[@]}"; do
    if [ -e "$root/$n" ] || [ -L "$root/$n" ]; then
      echo "  ✗ 執行後仍存在：$root/$n"
      return 1
    fi
  done
}
 
# remove_skill <skill 名稱...>：依 --global 決定範圍
remove_skill() { remove_skill_in "$UN_SCOPE" "$@"; }
 
# remove_skill_collection <repo> <判定名稱> [排除的 skill:項目 ...]
#   以 npx skills add <repo> --list 讀取清單（輸出格式未查證），與該範圍實際存在的資料夾取交集
#   清單讀不到或沒有判定名稱 → 只清判定名稱，並計為失敗
remove_skill_collection() {
  local repo="$1" rep="$2" root list d n ex skip names=()
  shift 2
  root=$(skills_root "$UN_SCOPE")
  if [ ! -d "$root/$rep" ]; then
    remove_skill_in "$UN_SCOPE" "$rep"
    return
  fi
  echo "  讀取 $repo 的 skill 清單"
  list=$(NO_COLOR=1 FORCE_COLOR=0 npx --yes skills@latest add "$repo" --list 2>/dev/null)
  if ! printf '%s\n' "$list" | find_token "$rep"; then
    echo "  ✗ 讀不到 $repo 的 skill 清單（或清單中沒有 $rep），只清除 $rep；其餘請用 npx skills remove 手動清除"
    remove_skill_in "$UN_SCOPE" "$rep"
    return 1
  fi
  for d in "$root"/*/; do
    [ -d "$d" ] || continue
    n=$(basename "$d")
    printf '%s\n' "$list" | find_token "$n" || continue
    skip=0
    for ex in "$@"; do
      if [ "${ex%%:*}" = "$n" ]; then
        case "$UN_TARGETS" in
          *" ${ex#*:} "*) ;;
          *) skip=1
             echo "  保留 $n（屬於必裝項目 ${ex#*:}，本次未指定清除）" ;;
        esac
      fi
    done
    [ $skip -eq 0 ] && names+=("$n")
  done
  remove_skill_in "$UN_SCOPE" "${names[@]}"
}
 
# remove_mcp <server 名稱>
#   注意：假設 claude mcp get 找不到時回傳非 0（未經文件證實）
remove_mcp() {
  local name="$1"
  if ! claude mcp get "$name" >/dev/null 2>&1; then
    echo "  未安裝 MCP $name，略過"
    return 20
  fi
  run_cmd claude mcp remove --scope user "$name"
}
 
un_run_item() {
  local item="${1//-/_}"   # 允許 ppt-visual 這種寫法
  local fn="uninstall_$item"
  if ! declare -F "$fn" >/dev/null; then
    echo "✗ 未知項目：$1"
    FAIL_LIST+=("$1（未知項目）")
    UN_FAILED="$UN_FAILED$item "
    return
  fi
  # 同一次執行只處理一次（marketplace 連帶清除 plugin 時也適用）
  case "$UN_DONE" in *" $item "*) return ;; esac
  UN_DONE="$UN_DONE$item "
  echo "▶ 清除 $item"
  "$fn"
  case $? in
    0)  CLEARED_LIST+=("$item") ;;
    20) NOT_INSTALLED_LIST+=("$item") ;;
    12) UNSUPPORTED_LIST+=("$item") ;;
    *)  FAIL_LIST+=("$item"); UN_FAILED="$UN_FAILED$item " ;;
  esac
}
 
print_un_summary() {
  echo
  echo "================ 清除摘要 ================"
  [ "$DRY_RUN" -eq 1 ] && echo "dry-run：未實際執行，「已清除」表示會執行清除指令"
  echo "已清除（${#CLEARED_LIST[@]}）：${CLEARED_LIST[*]}"
  echo "未安裝（${#NOT_INSTALLED_LIST[@]}）：${NOT_INSTALLED_LIST[*]}"
  echo "不支援，不計入失敗（${#UNSUPPORTED_LIST[@]}）：${UNSUPPORTED_LIST[*]}"
  echo "失敗（${#FAIL_LIST[@]}）：${FAIL_LIST[*]}"
  [ ${#FAIL_LIST[@]} -eq 0 ]
}
 
uninstall_main() {
  local all=0 a ans items=()
  while [ $# -gt 0 ]; do
    case "$1" in
      --dry-run) DRY_RUN=1 ;;
      -y)        ASSUME_YES=1 ;;
      --global)  UN_SCOPE=global ;;
      --all)     all=1 ;;
      -*)        echo "✗ 未知的選項：$1"; print_usage; return 2 ;;
      *)         break ;;
    esac
    shift
  done
  for a in "$@"; do
    case "$a" in
      -*) echo "✗ 選項必須放在項目名稱之前：$a"; print_usage; return 2 ;;
    esac
  done
  if [ $all -eq 1 ] && [ $# -gt 0 ]; then
    echo "✗ --all 不能和項目名稱一起使用"
    return 2
  fi
  if [ $all -eq 0 ] && [ $# -eq 0 ]; then
    print_usage
    return 0
  fi
 
  if [ $all -eq 1 ]; then
    # 依安裝的反向順序：MCPS → TOOLS → SKILLS → PLUGINS → MARKETPLACES
    for a in "${REQUIRED_MCPS[@]}" "${OPTIONAL_MCPS[@]}" \
             "${REQUIRED_TOOLS[@]}" "${OPTIONAL_TOOLS[@]}" \
             "${REQUIRED_SKILLS[@]}" "${OPTIONAL_SKILLS[@]}" \
             "${REQUIRED_PLUGINS[@]}" "${OPTIONAL_PLUGINS[@]}" \
             "${REQUIRED_MARKETPLACES[@]}" "${HIDDEN_MARKETPLACES[@]}"; do
      [ "$a" = claude_plugins_official_marketplace ] && continue
      items+=("$a")
    done
  else
    for a in "$@"; do items+=("${a//-/_}"); done
  fi
  UN_TARGETS=" ${items[*]} "
 
  echo "將清除以下項目（marketplace 會先連帶清除其底下的 plugin）："
  printf '  %s\n' "${items[@]}"
  if [ "$UN_SCOPE" = global ]; then
    echo "skill 範圍：全域"
  else
    echo "skill 範圍：專案（ai_copywriter 例外，一律清全域）"
  fi
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "dry-run：只列出指令，不會實際執行"
  elif [ "$ASSUME_YES" -eq 0 ]; then
    read -r -p "輸入 y 繼續，其他任意鍵取消：" ans
    case "$ans" in
      y|Y) ;;
      *) echo "已取消"; return 0 ;;
    esac
  fi
 
  for a in "${items[@]}"; do
    un_run_item "$a"
  done
  print_un_summary
}
 
main() {
  case "$1" in
    --list)
      print_list
      return 0 ;;
    --uninstall)
      shift
      uninstall_main "$@"
      return ;;
  esac
 
  local a
  for a in "$@"; do
    case "$a" in
      --dry-run|-y|--global|--all)
        echo "✗ $a 只能搭配 --uninstall 使用"
        print_usage
        return 2 ;;
    esac
  done
 
  local items
  if [ $# -gt 0 ]; then
    items=("$@")
  else
    items=(
      "${REQUIRED_MARKETPLACES[@]}"
      "${REQUIRED_PLUGINS[@]}"
      "${REQUIRED_SKILLS[@]}"
      "${REQUIRED_TOOLS[@]}"
      "${REQUIRED_MCPS[@]}"
    )
  fi
 
  for item in "${items[@]}"; do
    run_item "$item"
  done
 
  print_summary
}
 
main "$@"