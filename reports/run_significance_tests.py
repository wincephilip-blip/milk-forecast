"""
run_significance_tests.py
==========================
事後（post-hoc）統計分析腳本：對 _holdout_backtest.json 中之 4 個逐年誤差，
做：
  1. cohort MAPE 的 95% bootstrap 信賴區間（n=4, 重抽 2,000 次）
  2. cohort vs STL+linear 的配對 Wilcoxon signed-rank 檢定

論文 §2.5 中宣稱的「post-hoc 後處理 with scipy/numpy」就是這個腳本。
不在 milkfc 套件主管線內、是論文撰寫時的補充分析。

執行方式（在 ~/Milk_forecast 目錄下）:
  python3 reports/run_significance_tests.py
"""

import json
from pathlib import Path
import numpy as np
from scipy import stats

PROJ = Path(__file__).resolve().parent.parent

with open(PROJ / "snapshots" / "_holdout_backtest.json") as f:
    bt = json.load(f)

# 抓 cohort 與 STL+linear 之 4 年絕對誤差（皆於全國尺度，與 Table 3 一致）
# STL+linear 之 DHI 子樣本預測經 SF L4_farms（場數比例）還原為全國尺度
cohort_errs = []
stl_errs = []
for r in bt["rows"]:
    cohort_errs.append(r["cohort_err_pct"])
    pred_dhi = r["model_predictions"]["stl_linear"]
    sf = r["sf_l4_farms"]
    actual_full = r["full_actual_tons"]
    stl_full = pred_dhi * sf
    stl_errs.append((stl_full - actual_full) / actual_full * 100)

cohort_abs = np.abs(cohort_errs)
stl_abs = np.abs(stl_errs)

print("=" * 60)
print("Post-hoc 統計分析（論文 §3.4 數值之來源）")
print("=" * 60)
print(f"Cohort 4 年逐年誤差: {cohort_errs}")
print(f"  絕對誤差: {list(cohort_abs)}")
print(f"  平均 (MAPE): {np.mean(cohort_abs):.4f}%")
print()
print(f"STL+linear 4 年逐年誤差: {stl_errs}")
print(f"  絕對誤差: {list(stl_abs)}")
print(f"  平均 (MAPE): {np.mean(stl_abs):.4f}%")

# 1. Bootstrap CI
print()
print("--- Bootstrap CI for cohort MAPE ---")
np.random.seed(42)
boot = []
for _ in range(2000):
    sample = np.random.choice(cohort_abs, size=4, replace=True)
    boot.append(np.mean(sample))
ci_lo, ci_hi = np.percentile(boot, [2.5, 97.5])
print(f"95% bootstrap CI for cohort MAPE: ({ci_lo:.2f}%, {ci_hi:.2f}%)")
print(f"  論文 §3.4 寫: (0.21%, 5.49%)")

# 2. Wilcoxon
print()
print("--- Wilcoxon signed-rank test (cohort vs STL+linear) ---")
res = stats.wilcoxon(cohort_abs, stl_abs)
print(f"Wilcoxon W = {res.statistic}, p-value = {res.pvalue:.4f}")
print()
print(f"備註：n=4 之配對 Wilcoxon 之雙尾 p 最小可能為 1/8 = 0.125")
print(f"      若 4 對皆同向（cohort < STL）、W=0、p=0.125（理論值）")
