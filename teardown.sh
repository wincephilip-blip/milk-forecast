#!/bin/bash
# =====================================================================
# teardown.sh — 一鍵關閉 / 下架腳本
# =====================================================================
# 用法：
#   ./teardown.sh
#
# 提供 4 種關閉方式：
#   1. 只刪 GitHub Pages（網站立刻 404，repo 還在）
#   2. 把 repo 改私人（程式碼還在但別人看不到）
#   3. 刪除 main branch 內容但保留 repo（清空但保留 URL）
#   4. 完全刪除 repo（不可逆）
#
# 注意：方法 2、4 需要從 GitHub 網頁或 gh CLI 操作，
# 此腳本只處理本機 git 與 gh-pages 部分。

set -e

GIT_REPO="${GIT_REPO:-https://github.com/wincephilip-blip/milk-forecast.git}"
SOURCE_DIR="${SOURCE_DIR:-${HOME}/Milk_forecast}"

cd "${SOURCE_DIR}"

if [ ! -d ".git" ]; then
    echo "❌ 此目錄不是 git repo，沒有東西可以下架"
    exit 1
fi

cat << 'EOF'

==============================================================
請選擇下架方式：
==============================================================

  [1] 只刪 GitHub Pages（網站立刻 404，repo / 程式碼仍保留）
      → 刪除遠端 gh-pages branch
      → 5–10 分鐘後 https://wincephilip-blip.github.io/milk-forecast/ 變 404

  [2] 把 repo 改私人（最常用）
      → 此腳本會印指令給你；要從 GitHub 網頁或 gh CLI 執行
      → 立即生效，可隨時改回 public

  [3] 清空 main branch 但保留 repo（少見）
      → 推一個空 commit 清空 main
      → 適合「保留 repo URL 但讓內容消失」

  [4] 完全刪除整個 repo（不可逆，最徹底）
      → 此腳本會印指令給你；要從 GitHub 網頁或 gh CLI 執行

  [0] 取消

==============================================================

EOF

read -p "請輸入選項 [0-4]：" choice

case "${choice}" in
    1)
        echo ""
        echo "🗑 刪除 gh-pages branch..."
        if git ls-remote --heads origin gh-pages | grep -q gh-pages; then
            git push origin --delete gh-pages
            echo "✅ gh-pages 已刪除"
            echo "   5–10 分鐘後網站變 404"
        else
            echo "ℹ 遠端沒有 gh-pages branch（可能還沒部署過 Pages）"
        fi
        ;;
    2)
        echo ""
        echo "📋 請執行以下其中一個方法："
        echo ""
        echo "方法 A：GitHub 網頁"
        echo "  1. 打開 https://github.com/wincephilip-blip/milk-forecast/settings"
        echo "  2. 拉到底部 Danger Zone"
        echo "  3. 點 'Change repository visibility' → 'Make private'"
        echo "  4. 輸入 repo 全名確認"
        echo ""
        echo "方法 B：gh CLI（需先 brew install gh && gh auth login）"
        echo "  gh repo edit wincephilip-blip/milk-forecast --visibility private"
        echo ""
        ;;
    3)
        echo ""
        read -p "⚠ 確認要清空 main branch 內容？ [y/N] " yn
        case "$yn" in
            [Yy]*)
                echo "🗑 清空 main branch..."
                # 建立 orphan branch
                git checkout --orphan empty-temp
                git rm -rf . > /dev/null 2>&1 || true
                echo "# Repository archived" > README.md
                git add README.md
                git commit -m "Archive: clear repository contents"
                git branch -M main
                git push -f origin main
                echo "✅ main branch 已清空"
                echo "ℹ 本機程式碼已被刪除，請從備份還原"
                ;;
            *) echo "❌ 取消" ;;
        esac
        ;;
    4)
        echo ""
        echo "📋 請執行以下其中一個方法："
        echo ""
        echo "方法 A：GitHub 網頁"
        echo "  1. 打開 https://github.com/wincephilip-blip/milk-forecast/settings"
        echo "  2. 拉到底部 Danger Zone"
        echo "  3. 點 'Delete this repository'"
        echo "  4. 輸入 repo 全名 'wincephilip-blip/milk-forecast' 確認"
        echo ""
        echo "方法 B：gh CLI"
        echo "  gh repo delete wincephilip-blip/milk-forecast --yes"
        echo ""
        echo "⚠ 警告：刪除後不可復原，包含 issues、wiki、stargazers 都會消失"
        echo "   被 fork 的副本不會被刪"
        ;;
    0|*)
        echo "❌ 取消"
        exit 0
        ;;
esac

echo ""
echo "完成。"
