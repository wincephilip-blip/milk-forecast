#!/bin/bash
# 部署 4 套儀表板到 GitHub Pages
#
# 設定：第一次跑前修改下方 GIT_REPO 為你的 repo URL（或用環境變數覆寫）
# 用法：./deploy.sh
#       GIT_REPO=https://github.com/me/milk-forecast.git ./deploy.sh
#
set -e

# === 設定 ===
GIT_REPO="${GIT_REPO:-https://github.com/wincephilip-blip/milk-forecast.git}"
DEPLOY_DIR="${DEPLOY_DIR:-/tmp/milkfc-deploy}"
SOURCE_DIR="${SOURCE_DIR:-${HOME}/Milk_forecast}"

# 部署清單（timeseries 是首頁）
# 預設只部署 timeseries（聚合資料、無場代號、最安全）
# 如需公開場級儀表板、把下方註解的 3 行取消註解
HTML_FILES=(
  "timeseries.html"
  # "dashboard.html"   # ⚠️ 含 346 個 farm_id 場代號
  # "seasonal.html"    # ⚠️ 含場別月度分布
  # "lactation.html"   # ⚠️ 含個體牛/場泌乳曲線
)

# === 檢查 ===
if [ ! -f "${SOURCE_DIR}/timeseries.html" ]; then
    echo "❌ 找不到 ${SOURCE_DIR}/timeseries.html"
    echo "   請先跑：python3 -m milkfc forecast-ts --dashboard"
    exit 1
fi

if [[ "$GIT_REPO" == *"YOUR_USER"* ]]; then
    echo "❌ 請編輯 deploy.sh 把 GIT_REPO 改成你的 repo URL"
    echo "   或用環境變數：GIT_REPO=https://github.com/你/milk-forecast.git ./deploy.sh"
    exit 1
fi

# === 部署 ===
echo "▶ 準備部署儀表板 → GitHub Pages"
echo "   Repo: $GIT_REPO"
echo "   來源: $SOURCE_DIR"
echo

# 確保部署目錄乾淨
rm -rf "$DEPLOY_DIR"
mkdir -p "$DEPLOY_DIR"

# 複製所有可用的儀表板
copied_count=0
for f in "${HTML_FILES[@]}"; do
    if [ -f "${SOURCE_DIR}/$f" ]; then
        cp "${SOURCE_DIR}/$f" "${DEPLOY_DIR}/$f"
        size=$(du -h "${SOURCE_DIR}/$f" | cut -f1)
        echo "   ✓ $f ($size)"
        copied_count=$((copied_count + 1))
    else
        echo "   ⚠ $f 不存在、略過"
    fi
done

if [ $copied_count -eq 0 ]; then
    echo "❌ 沒有任何儀表板檔案可部署"
    exit 1
fi

# timeseries 設為首頁（複製成 index.html）
cp "${SOURCE_DIR}/timeseries.html" "${DEPLOY_DIR}/index.html"
echo "   ✓ index.html ← timeseries.html (首頁)"
echo

# 移除頂部導覽列中沒有部署的連結（避免 404）
echo "▶ 清理導覽列、移除未部署的連結"
for missing in dashboard.html seasonal.html lactation.html timeseries.html; do
    if [ ! -f "${DEPLOY_DIR}/$missing" ]; then
        for deployed in "${DEPLOY_DIR}"/*.html; do
            # macOS 與 Linux 兼容寫法：先寫 .bak 再刪
            sed -i.bak "/href=\"${missing}\"/d" "$deployed"
            rm -f "${deployed}.bak"
        done
        echo "   ✓ 已移除指向 $missing 的導覽連結"
    fi
done
echo

# 寫一個簡短 README
cat > "$DEPLOY_DIR/README.md" << EOF
# 全國牛乳產量預測儀表板 / National Milk Production Forecasting

開啟 \`index.html\`（同 \`timeseries.html\`）即可。

## 4 套儀表板

| 檔案 | 用途 |
|---|---|
| \`timeseries.html\` (首頁) | 主要時間序列預測、含 What-If 情境 |
| \`dashboard.html\` | 場別預測 + 歷史驗證 |
| \`seasonal.html\` | 各場各年 1-12 月乳量分布 |
| \`lactation.html\` | 場別/全國/個體牛泌乳曲線 |

頂部導覽列可互相切換。

## 資料來源

- DHI 月度紀錄
- 農業部〈畜牧生產〉年報
- 農業部〈在養量比較〉季報
- 農業部〈牛乳產量〉年報（僅作驗證、不入預測）

## 方法

時序模型（stl_linear / holt_winters / sarima / prophet / naive_seasonal /
neural_prophet）+ Cohort 結構模型 + Level 4 SF 涵蓋率還原校正。
詳情見儀表板「📖 方法論」章節。

最後更新：$(date "+%Y-%m-%d %H:%M")
EOF

# 加 .nojekyll（避免 GitHub Pages 走 Jekyll、影響底線開頭的檔名）
touch "$DEPLOY_DIR/.nojekyll"

# Git push（force、shallow）→ gh-pages branch
# 注意：force-push 到 gh-pages、不動 main（main 留給原始碼）
cd "$DEPLOY_DIR"
git init -q
git checkout -q -B gh-pages
git add .
git -c user.email="deploy@local" -c user.name="deploy" \
    commit -q -m "Auto-deploy $(date '+%Y-%m-%d %H:%M')"

echo "▶ Push 到 $GIT_REPO（branch: gh-pages）..."
git push -fq "$GIT_REPO" gh-pages

echo
echo "✅ 部署完成（$copied_count 個儀表板）"
echo
echo "下一步："
echo "  1. 到 GitHub repo Settings → Pages"
echo "  2. Source 選 'Deploy from a branch'、Branch 'gh-pages' / '/(root)'、按 Save"
echo "  3. 等 1-2 分鐘、訪問你的 GitHub Pages URL"
echo "     (預設格式：https://你的帳號.github.io/repo-名稱)"
echo
echo "ℹ 設計說明：本腳本只動 gh-pages branch、不動 main。"
echo "   main = 原始碼來源、gh-pages = dashboard 部署快照、兩者完全分離。"
