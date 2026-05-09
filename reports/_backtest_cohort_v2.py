"""Backtest cohort_v2 vs cohort_simple，跨 phase 比較。

依 v2 flag 組合產生不同情境的 4 年 backtest（2021-2024），
輸出 JSON 與 console 表格供決策。
"""
import sys
import json
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

import pandas as pd
import numpy as np
from milkfc.forecast.cohort_model import forecast_cohort_simple
from milkfc.forecast.cohort_model_v2 import forecast_cohort_v2

# 全國公告產量
df = pd.read_excel(PROJ / 'raw_data' / '08--畜牧生產及貿易_牛乳產量.ods', engine='odf')
df.columns = ['c0','yroc','c2','prod']
df = df[['yroc','prod']].dropna()
df['year'] = df['yroc'].astype(str).str.replace('年','').astype(int) + 1911
official = dict(zip(df['year'], df['prod']))

YEARS = [2021, 2022, 2023, 2024]


def run_scenario(name: str, fn, **flags):
    """Run a backtest scenario, return per-year errors and stats."""
    rows = []
    for y in YEARS:
        r = fn(target_year=y, history_max_year=y - 1, **flags)
        if not r.get("success"):
            continue
        pred = r["annual_total_tons"]
        actual = official[y]
        err = (pred - actual) / actual * 100
        rows.append({
            "year": y,
            "pred": pred,
            "actual": actual,
            "err_pct": err,
            "Q_pred": r.get("predicted_daily_yield_kg"),
            "N_pred": r.get("predicted_cows"),
            "r_pred": r.get("productivity_ratio_target"),
        })
    abs_errs = [abs(r["err_pct"]) for r in rows]
    biases = [r["err_pct"] for r in rows]
    mape = float(np.mean(abs_errs))
    bias = float(np.mean(biases))
    return {
        "name": name,
        "rows": rows,
        "MAPE": mape,
        "Bias": bias,
    }


def fmt(s):
    return (f'{s["name"]:>30s} | MAPE {s["MAPE"]:5.2f}% | bias {s["Bias"]:+5.2f}% | '
            + ' '.join(f'{r["err_pct"]:+6.2f}%' for r in s["rows"]))


print("=" * 100)
print("Cohort v1 vs v2 phase 1 (Q monthly STL) — 4 年 backtest")
print("=" * 100)
print(f'{"Scenario":>30s} | {"MAPE":>6s} | {"Bias":>7s} | '
      f'{"2021":>6s} {"2022":>6s} {"2023":>6s} {"2024":>6s}')
print("-" * 100)

scenarios = []

# v1 baseline
s = run_scenario("v1 simple (paper)", forecast_cohort_simple)
print(fmt(s))
scenarios.append(s)

# v2 default (應與 v1 相同)
s = run_scenario("v2 default (= v1)", forecast_cohort_v2)
print(fmt(s))
scenarios.append(s)

# v2 phase 1: q monthly STL
s = run_scenario("v2 phase 1 (Q monthly STL)",
                  forecast_cohort_v2, q_projection='monthly_stl')
print(fmt(s))
scenarios.append(s)

# v2 phase 2: N quarterly
s = run_scenario("v2 phase 2 (N quarterly)",
                  forecast_cohort_v2, n_projection='quarterly')
print(fmt(s))
scenarios.append(s)

# v2 phase 1+2 combined
s = run_scenario("v2 phase 1+2 (Q+N combined)",
                  forecast_cohort_v2,
                  q_projection='monthly_stl',
                  n_projection='quarterly')
print(fmt(s))
scenarios.append(s)

print("-" * 100)
print("Phase 3 nowcast 情境（n_projection='quarterly' + as_of_date）：")
print("-" * 100)


def run_nowcast_scenario(name: str, as_of_offset_month: int):
    """跑 nowcast 情境：每年 backtest 設 as_of_date = {y}-{month}-01"""
    rows = []
    for y in YEARS:
        as_of = f"{y}-{as_of_offset_month:02d}-01"
        r = forecast_cohort_v2(target_year=y, history_max_year=y - 1,
                                 n_projection='quarterly',
                                 as_of_date=as_of)
        if not r.get("success"):
            continue
        pred = r["annual_total_tons"]
        actual = official[y]
        err = (pred - actual) / actual * 100
        rows.append({
            "year": y, "pred": pred, "actual": actual, "err_pct": err,
            "as_of": as_of,
        })
    abs_errs = [abs(r["err_pct"]) for r in rows]
    biases = [r["err_pct"] for r in rows]
    return {
        "name": name,
        "rows": rows,
        "MAPE": float(np.mean(abs_errs)),
        "Bias": float(np.mean(biases)),
    }


# 純外推（年初）
s = run_nowcast_scenario("Phase 3: 年初純外推 (as_of=Mar)", 3)
print(fmt(s)); scenarios.append(s)

# Q1 已公告（5 月後）
s = run_nowcast_scenario("Phase 3: nowcast +Q1 (as_of=Jun)", 6)
print(fmt(s)); scenarios.append(s)

# Q1+Q2 已公告（8 月後）
s = run_nowcast_scenario("Phase 3: nowcast +Q1+Q2 (as_of=Sep)", 9)
print(fmt(s)); scenarios.append(s)

# Q1+Q2+Q3 已公告（11 月後）
s = run_nowcast_scenario("Phase 3: nowcast +Q1+Q2+Q3 (as_of=Dec)", 12)
print(fmt(s)); scenarios.append(s)

print("-" * 100)
print("Phase 4 r_t 自適應（疊加在 phase 3 +Q1 nowcast 上）：")
print("-" * 100)


def run_p4_scenario(name: str, as_of_offset_month: int, r_window='adaptive'):
    rows = []
    for y in YEARS:
        as_of = f"{y}-{as_of_offset_month:02d}-01"
        r = forecast_cohort_v2(target_year=y, history_max_year=y - 1,
                                 n_projection='quarterly',
                                 as_of_date=as_of,
                                 r_window=r_window)
        if not r.get("success"):
            continue
        pred = r["annual_total_tons"]
        actual = official[y]
        err = (pred - actual) / actual * 100
        rows.append({"year": y, "pred": pred, "actual": actual,
                       "err_pct": err, "as_of": as_of})
    abs_errs = [abs(r["err_pct"]) for r in rows]
    biases = [r["err_pct"] for r in rows]
    return {"name": name, "rows": rows,
            "MAPE": float(np.mean(abs_errs)),
            "Bias": float(np.mean(biases))}


# Phase 4 ensemble + Q1 nowcast (主推)
s = run_p4_scenario("Phase 4 ensemble + Q1 nowcast", 6, 'adaptive')
print(fmt(s)); scenarios.append(s)

# Phase 4 ensemble + Q1+Q2 nowcast
s = run_p4_scenario("Phase 4 ensemble + Q1+Q2 nowcast", 9, 'adaptive')
print(fmt(s)); scenarios.append(s)

# Phase 4 ensemble + Q1+Q2+Q3 nowcast
s = run_p4_scenario("Phase 4 ensemble + Q1+Q2+Q3 nowcast", 12, 'adaptive')
print(fmt(s)); scenarios.append(s)

# Phase 4 純外推
s = run_p4_scenario("Phase 4 ensemble + 純外推", 3, 'adaptive')
print(fmt(s)); scenarios.append(s)

print("-" * 100)

# 額外：詳細顯示 v2 phase 1 之 Q 投影 vs v1
print("\nQ projection 比較（kg/day）:")
print(f'{"Year":>6} {"v1 Q":>10} {"v2-phase1 Q":>14} {"diff":>8}')
for y in YEARS:
    r1 = forecast_cohort_simple(target_year=y, history_max_year=y-1)
    r2 = forecast_cohort_v2(target_year=y, history_max_year=y-1, q_projection='monthly_stl')
    q1 = r1['predicted_daily_yield_kg']
    q2 = r2['predicted_daily_yield_kg']
    print(f'{y:>6} {q1:>10.3f} {q2:>14.3f} {q2-q1:>+8.3f}')

# 輸出 JSON
out = PROJ / 'snapshots' / '_holdout_backtest_v2.json'
out.write_text(json.dumps({
    "scenarios": [
        {
            "name": s["name"],
            "MAPE": s["MAPE"],
            "Bias": s["Bias"],
            "per_year": s["rows"],
        }
        for s in scenarios
    ],
}, ensure_ascii=False, indent=2))
print(f"\nSaved: {out}")
