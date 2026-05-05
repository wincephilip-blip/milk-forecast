#!/bin/bash
# =====================================================================
# init_repo.sh — 一鍵 git init + 安全檢查 + 推到 GitHub main branch
# =====================================================================
# 用法：
#   cd ~/Milk_forecast
#   ./init_repo.sh
#
# 這個腳本會：
#   1. 確認 .gitignore 存在（避免不小心推敏感資料）
#   2. git init（如果還沒）
#   3. 安全檢查：掃描將要推的檔案是否含 farm_id / cow_id / API key
#   4. 詢問是否確認推送
#   5. git add / commit / push 到 main branch
#
# 不會動到 gh-pages branch（那是 deploy.sh 的工作）

set -e

GIT_REPO="${GIT_REPO:-https://github.com/wincephilip-blip/milk-forecast.git}"
SOURCE_DIR="${SOURCE_DIR:-${HOME}/Milk_forecast}"

cd "${SOURCE_DIR}"

# ---- Step 1：確認 .gitignore 存在 ----
if [ ! -f ".gitignore" ]; then
    echo "❌ 找不到 .gitignore"
    echo "   為了避免不小心推敏感資料，必須先建立 .gitignore"
    exit 1
fi
echo "✅ .gitignore 已就位"

# ---- Step 2：git init（如果還沒）----
if [ ! -d ".git" ]; then
    echo ""
    echo "📦 第一次部署，初始化 git repo..."
    git init
    git branch -M main
    git remote add origin "${GIT_REPO}" 2>/dev/null || git remote set-url origin "${GIT_REPO}"
    echo "✅ git init 完成"
else
    echo "✅ git repo 已存在"
    # 確認 remote 設對
    if ! git remote get-url origin > /dev/null 2>&1; then
        git remote add origin "${GIT_REPO}"
    fi
fi

# ---- Step 3：安全檢查 ----
echo ""
echo "🔍 進行安全掃描..."

# 列出將會推送的檔案（已套用 .gitignore）
git add --intent-to-add --all > /dev/null 2>&1
TRACKED_FILES=$(git ls-files --others --cached --exclude-standard 2>/dev/null)

if [ -z "${TRACKED_FILES}" ]; then
    echo "⚠ 沒有檔案會被推送（可能 .gitignore 把全部都排除了）"
    exit 1
fi

# 掃描敏感字串
HITS_FARM=$(echo "${TRACKED_FILES}" | xargs grep -l "farm_id" 2>/dev/null || true)
HITS_COW=$(echo "${TRACKED_FILES}" | xargs grep -l "cow_id" 2>/dev/null || true)
HITS_KEY=$(echo "${TRACKED_FILES}" | xargs grep -lE "(API_KEY|SECRET|PASSWORD|BEARER)\\s*=\\s*['\"][A-Za-z0-9]" 2>/dev/null || true)

WARN_COUNT=0

if [ -n "${HITS_FARM}" ]; then
    echo ""
    echo "⚠ 發現含 farm_id 的檔案："
    echo "${HITS_FARM}" | sed 's/^/    /'
    WARN_COUNT=$((WARN_COUNT + 1))
fi

if [ -n "${HITS_COW}" ]; then
    echo ""
    echo "⚠ 發現含 cow_id 的檔案："
    echo "${HITS_COW}" | sed 's/^/    /'
    WARN_COUNT=$((WARN_COUNT + 1))
fi

if [ -n "${HITS_KEY}" ]; then
    echo ""
    echo "🚨 發現可能含密鑰的檔案（高風險）："
    echo "${HITS_KEY}" | sed 's/^/    /'
    WARN_COUNT=$((WARN_COUNT + 1))
fi

# 程式碼提到變數名 farm_id（不算敏感，因為是定義不是資料）
# 所以只警示不阻擋；列出檔案讓使用者確認
if [ ${WARN_COUNT} -gt 0 ]; then
    echo ""
    echo "⚠ 上述檔案需要人工確認：是「程式碼提到變數名」(OK) 還是「實際酪農場代號資料」(危險)？"
    read -p "確認上述都是程式碼變數名、可以推送？ [y/N] " yn
    case "$yn" in
        [Yy]*) echo "✅ 使用者確認，繼續" ;;
        *) echo "❌ 使用者取消"; exit 1 ;;
    esac
else
    echo "✅ 安全掃描通過：未發現 farm_id / cow_id / 密鑰"
fi

# ---- Step 4：列出將推送的檔案數量 ----
N_FILES=$(echo "${TRACKED_FILES}" | wc -l | xargs)
TOTAL_SIZE=$(echo "${TRACKED_FILES}" | xargs du -ch 2>/dev/null | tail -1 | awk '{print $1}')

echo ""
echo "📊 將要推送：${N_FILES} 個檔案，總大小約 ${TOTAL_SIZE}"
read -p "確認推到 ${GIT_REPO} main branch？ [y/N] " yn
case "$yn" in
    [Yy]*) ;;
    *) echo "❌ 使用者取消"; exit 1 ;;
esac

# ---- Step 5：git add / commit / push ----
echo ""
echo "📤 開始推送..."

git add .
COMMIT_MSG="${COMMIT_MSG:-Update milkfc pipeline ($(date +%Y-%m-%d))}"

if git diff --cached --quiet; then
    echo "ℹ 沒有變更需要 commit"
else
    git commit -m "${COMMIT_MSG}"
    echo "✅ commit 完成"
fi

# Push（如果是第一次需要 -u）
if git rev-parse --verify origin/main > /dev/null 2>&1; then
    git push origin main
else
    git push -u origin main
fi

echo ""
echo "🎉 推送完成！"
echo "   程式碼：${GIT_REPO}"
echo "   論文網站：https://wincephilip-blip.github.io/milk-forecast/ （由 deploy.sh 部署）"
echo ""
echo "如要關閉，跑：./teardown.sh"
