"""validate_2026.py
=================
2026 年實測驗收腳本。

執行情境（按時間順序）：
    A. 2026-01 ~ 2026-05（現在）
        無 target year 季報、純外推
        執行：python3 reports/validate_2026.py --as-of 2026-05-09
        預期 v2 phase 4 點估計 ≈ 46.01 萬公噸

    B. 2026-06（2026Q1 季報公告後）
        執行：python3 reports/validate_2026.py --as-of 2026-07-01
        預期 nowcast 後點估計於 2026Q1 真值附近

    C. 2026-09（2026Q2 公告後）/ 2026-12（2026Q3 公告後）
        執行：python3 reports/validate_2026.py --as-of 2026-09-01
              python3 reports/validate_2026.py --as-of 2026-12-01

    D. 2027-Q3（2026 年報公告後）
        執行：python3 reports/validate_2026.py --final
        計算各情境誤差，輸出最終驗收表

每次執行後輸出 snapshots/_validation_2026_{as_of}.json。
"""
import argparse
import json
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

from milkfc.forecast.cohort_model import forecast_cohort_simple
from milkfc.forecast.cohort_model_v2 import forecast_cohort_v2


def run_validation(as_of: str, final: bool = False):
    target = 2026

    print(f"\n{'=' * 70}")
    print(f"2026 預測驗收（as_of = {as_of}）")
    print(f"{'=' * 70}\n")

    results = {
        "as_of_date": as_of,
        "target_year": target,
        "predictions": {},
    }

    # v1 simple（論文版本，純外推）
    r1 = forecast_cohort_simple(target_year=target)
    results["predictions"]["v1_simple"] = {
        "annual_total_tons": r1["annual_total_tons"],
        "predicted_cows": r1["predicted_cows"],
        "predicted_daily_yield_kg": r1["predicted_daily_yield_kg"],
        "productivity_ratio_target": r1["productivity_ratio_target"],
    }
    print(f"v1 simple                : {r1['annual_total_tons']:>9,.0f} 公噸  "
          f"({r1['annual_total_tons']/10000:.2f} 萬公噸)")

    # v2 phase 3 純外推
    r2_p3 = forecast_cohort_v2(target_year=target,
                                 n_projection='quarterly',
                                 as_of_date=as_of,
                                 nowcast_mode='off')
    results["predictions"]["v2_phase3_extrap"] = {
        "annual_total_tons": r2_p3["annual_total_tons"],
        "predicted_cows": r2_p3["predicted_cows"],
        "predicted_daily_yield_kg": r2_p3["predicted_daily_yield_kg"],
        "productivity_ratio_target": r2_p3["productivity_ratio_target"],
    }
    print(f"v2 phase 3 純外推         : {r2_p3['annual_total_tons']:>9,.0f} 公噸  "
          f"({r2_p3['annual_total_tons']/10000:.2f} 萬公噸)")

    # v2 phase 4 + auto nowcast
    r2_p4 = forecast_cohort_v2(target_year=target,
                                 n_projection='quarterly',
                                 r_window='adaptive',
                                 as_of_date=as_of,
                                 nowcast_mode='auto')
    nq = r2_p4['v2_config'].get('nowcast_mode_actual', 'auto')
    results["predictions"]["v2_phase4_auto"] = {
        "annual_total_tons": r2_p4["annual_total_tons"],
        "predicted_cows": r2_p4["predicted_cows"],
        "predicted_daily_yield_kg": r2_p4["predicted_daily_yield_kg"],
        "productivity_ratio_target": r2_p4["productivity_ratio_target"],
        "v2_config": r2_p4["v2_config"],
    }
    print(f"v2 phase 4 (auto)        : {r2_p4['annual_total_tons']:>9,.0f} 公噸  "
          f"({r2_p4['annual_total_tons']/10000:.2f} 萬公噸)")

    # final 模式：拿真值比對
    if final:
        try:
            import pandas as pd
            df = pd.read_excel(PROJ / 'raw_data' / '08--畜牧生產及貿易_牛乳產量.ods',
                                engine='odf')
            df.columns = ['c0', 'yroc', 'c2', 'prod']
            df = df[['yroc', 'prod']].dropna()
            df['year'] = df['yroc'].astype(str).str.replace('年', '').astype(int) + 1911
            actual = df[df['year'] == target]['prod'].iloc[0]
            results["actual"] = float(actual)

            print(f"\n{'=' * 70}")
            print(f"最終驗收（actual = {actual:,.0f} 公噸 = {actual/10000:.2f} 萬公噸）")
            print(f"{'=' * 70}")
            for name, info in results["predictions"].items():
                err = (info["annual_total_tons"] - actual) / actual * 100
                info["err_pct"] = err
                print(f"  {name:>30s}: err = {err:+.2f}%")
        except (FileNotFoundError, IndexError):
            print("\n2026 年實際產量資料尚未公告、無法做 final 驗收")

    # 存檔
    out_name = f"_validation_2026_{as_of.replace('-', '')}.json"
    out_path = PROJ / 'snapshots' / out_name
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"\nSaved: {out_path}")

    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--as-of', default=None,
                     help='決策日 (ISO YYYY-MM-DD)，預設今天')
    ap.add_argument('--final', action='store_true',
                     help='final 模式、與實際 2026 年產量比對')
    args = ap.parse_args()

    if args.as_of is None:
        from datetime import date
        args.as_of = date.today().isoformat()

    run_validation(args.as_of, final=args.final)


if __name__ == '__main__':
    main()
