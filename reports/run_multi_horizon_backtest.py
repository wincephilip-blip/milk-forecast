"""
run_multi_horizon_backtest.py
===============================
擴展 holdout backtest 到 24 個月與 36 個月之預測 horizon。

實驗設計（凍結訓練、遞迴預測）:
  h=12 (基準):
    cutoff=2020 → predict 2021
    cutoff=2021 → predict 2022
    cutoff=2022 → predict 2023
    cutoff=2023 → predict 2024
    (4 個 holdout, 與既有 _holdout_backtest.json 結果應一致)

  h=24:
    cutoff=2020 → predict 2021 + 2022 (sum, 用同一份訓練資料)
    cutoff=2021 → predict 2022 + 2023
    cutoff=2022 → predict 2023 + 2024
    (3 個 holdout)

  h=36:
    cutoff=2020 → predict 2021 + 2022 + 2023
    cutoff=2021 → predict 2022 + 2023 + 2024
    (2 個 holdout)

執行方式（在 ~/Milk_forecast 目錄下）:
  python3 reports/run_multi_horizon_backtest.py

輸出:
  snapshots/_multi_horizon_backtest.json
"""

import json
import sys
from pathlib import Path

# 確保可以 import milkfc
PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

from milkfc.forecast.cohort_model import forecast_cohort_simple
from milkfc.data.national_stats import parse_all_quarterly
import pandas as pd


def get_actual_annual_production():
    """讀取農業部公告之全國年度生乳產量 (公噸)。"""
    raw_dir = PROJ / "raw_data"
    df = pd.read_excel(raw_dir / "08--畜牧生產及貿易_牛乳產量.ods", engine="odf")
    df.columns = ["c0", "yroc", "c2", "prod"]
    df = df[["yroc", "prod"]].dropna()

    def parse_yr(s):
        s = str(s).strip()
        return int(s[:-1]) + 1911 if s.endswith("年") else None

    df["year"] = df["yroc"].map(parse_yr)
    df = df.dropna(subset=["year"]).copy()
    df["year"] = df["year"].astype(int)
    df["prod"] = pd.to_numeric(df["prod"], errors="coerce")
    return dict(zip(df["year"], df["prod"]))


def predict_year(cutoff_year: int, target_year: int) -> float:
    """以 cutoff_year 為訓練截止、預測 target_year 之年度產量。"""
    result = forecast_cohort_simple(
        target_year=target_year,
        horizon_months=12,
        history_min_year=2018,
        history_max_year=cutoff_year,  # 凍結訓練資料
        apply_productivity_correction=True,
    )
    if not result.get("success", False):
        # forecast_cohort_simple 沒有明確 success 欄位，查 annual_total_tons
        pass
    return float(result.get("annual_total_tons", 0))


def run_horizon(horizon_months: int, actual_prod: dict) -> list:
    """跑單一 horizon 的所有 holdout 並回傳結果。"""
    rows = []
    n_years = horizon_months // 12  # 多少個年度

    # cutoff 範圍：cutoff+1 到 cutoff+n_years 都要有實際資料 (≤2024)
    max_cutoff = 2024 - n_years
    min_cutoff = 2020  # 至少有 2018-2020 三年訓練
    cutoffs = list(range(min_cutoff, max_cutoff + 1))

    for cutoff in cutoffs:
        target_years = list(range(cutoff + 1, cutoff + 1 + n_years))
        # 只要有一個 target year 缺實際資料就跳過
        if any(y not in actual_prod for y in target_years):
            continue

        predictions = []
        for ty in target_years:
            pred = predict_year(cutoff_year=cutoff, target_year=ty)
            predictions.append(pred)

        actuals = [actual_prod[y] for y in target_years]
        sum_pred = sum(predictions)
        sum_actual = sum(actuals)
        err_pct = (sum_pred - sum_actual) / sum_actual * 100

        row = {
            "horizon_months": horizon_months,
            "training_cutoff_year": cutoff,
            "target_years": target_years,
            "individual_predictions_t": [round(p, 1) for p in predictions],
            "individual_actuals_t": [round(a, 1) for a in actuals],
            "sum_predicted_t": round(sum_pred, 1),
            "sum_actual_t": round(sum_actual, 1),
            "error_pct": round(err_pct, 4),
        }
        rows.append(row)
        print(
            f"  h={horizon_months}m, cutoff={cutoff} → predict {target_years}: "
            f"pred={sum_pred:,.0f} t / actual={sum_actual:,.0f} t "
            f"(err={err_pct:+.2f}%)"
        )

    return rows


def main():
    print("=" * 70)
    print("Multi-horizon Backtest: cohort 結構式於 12/24/36 個月之精度比較")
    print("=" * 70)

    actual = get_actual_annual_production()
    print(f"\n讀取到 {len(actual)} 年全國產量資料 ({min(actual)}–{max(actual)})")
    print(f"關鍵年: {dict([(y, actual[y]) for y in [2021, 2022, 2023, 2024] if y in actual])}")

    all_rows = []
    summary = {}

    for h in [12, 24, 36]:
        print(f"\n--- horizon = {h} 個月 ---")
        rows = run_horizon(h, actual)
        all_rows.extend(rows)

        if rows:
            errors = [r["error_pct"] for r in rows]
            mape = sum(abs(e) for e in errors) / len(errors)
            bias = sum(errors) / len(errors)
            summary[f"h_{h}m"] = {
                "n_holdouts": len(rows),
                "mape_pct": round(mape, 4),
                "bias_pct": round(bias, 4),
                "errors": [round(e, 4) for e in errors],
            }
            print(f"  → MAPE = {mape:.2f}%, Bias = {bias:+.2f}% (n={len(rows)})")

    # 輸出結果
    out_path = PROJ / "snapshots" / "_multi_horizon_backtest.json"
    out_path.parent.mkdir(exist_ok=True)
    output = {
        "experiment": "Cohort multi-horizon backtest (12/24/36 months)",
        "design": {
            "h=12": "predict 1 year ahead, 4 holdouts (2021-2024)",
            "h=24": "predict 2 consecutive years (sum) ahead, 3 holdouts",
            "h=36": "predict 3 consecutive years (sum) ahead, 2 holdouts",
        },
        "rows": all_rows,
        "summary": summary,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n結果已存至: {out_path}")
    print("\n" + "=" * 70)
    print("總結")
    print("=" * 70)
    for h_label, stats in summary.items():
        print(
            f"  {h_label}: n={stats['n_holdouts']}, "
            f"MAPE={stats['mape_pct']:.2f}%, Bias={stats['bias_pct']:+.2f}%"
        )


if __name__ == "__main__":
    main()
