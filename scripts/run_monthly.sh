#!/usr/bin/env bash
# milkfc 月度自動執行腳本（v0.2 - 一鍵全跑）
# 用 monthly 命令一次產出三組儀表板 + 告警摘要
#
# 排程建議：每月 5 號 03:00（DHI 通常月初發布，留 5 天緩衝）
# 加到 crontab:  0 3 5 * * /Users/tu/Milk_forecast/scripts/run_monthly.sh

set -euo pipefail

ROOT="${MILKFC_ROOT:-/Users/tu/Milk_forecast}"
LOG_DIR="$ROOT/logs"
mkdir -p "$LOG_DIR"

DATE=$(date +%Y%m%d)
LOG_FILE="$LOG_DIR/run_$DATE.log"

echo "=== milkfc monthly run @ $(date -Iseconds) ===" >> "$LOG_FILE"

cd "$ROOT"
[ -f .venv/bin/activate ] && source .venv/bin/activate

# === 資料驗證（失敗就停） ===
if ! python3 -m milkfc validate >> "$LOG_FILE" 2>&1; then
    echo "VALIDATION FAILED — 已停止" >> "$LOG_FILE"
    exit 1
fi

# === 一鍵全跑：預測 + 月度分布 + 泌乳曲線 + 異常告警（cron 模式跳過互動）===
python3 -m milkfc monthly --non-interactive >> "$LOG_FILE" 2>&1

echo "=== Done @ $(date -Iseconds) ===" >> "$LOG_FILE"

# 保留最近 90 天的 log
find "$LOG_DIR" -name "run_*.log" -mtime +90 -delete
