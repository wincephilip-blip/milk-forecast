"""回測工具"""
import pandas as pd
import numpy as np

def actual_monthly_milk(d: pd.DataFrame, start: str, end: str) -> dict:
    m = (d["sample_date"] >= start) & (d["sample_date"] <= end) & d["milk_kg"].notna()
    s = d[m].copy()
    s["yyyymm"] = s["sample_date"].dt.to_period("M").astype(str)
    monthly_per_cow = s.groupby(["yyyymm","cow_id"])["milk_kg"].mean()
    return (monthly_per_cow.groupby(level=0).sum() * 30).to_dict()

def run_backtest(forecast_df: pd.DataFrame, actual: dict,
                 target_year: int) -> dict:
    """加上實際值並計算 MAPE/bias/coverage."""
    fc = forecast_df.copy()
    fc["actual"] = fc["yyyymm"].map(actual)
    fc["err_pct"] = (fc["p50"] - fc["actual"]) / fc["actual"] * 100

    mask = fc["yyyymm"].str.startswith(str(target_year)) & fc["actual"].notna()
    if mask.sum() == 0:
        return {"forecast": fc, "mape": None, "bias": None, "coverage": None,
                "n_months": 0}

    mape = float(fc.loc[mask, "err_pct"].abs().mean())
    bias = float(fc.loc[mask, "err_pct"].mean())
    in_band = (fc["actual"] >= fc["p10"]) & (fc["actual"] <= fc["p90"])
    coverage = float((mask & in_band).sum() / mask.sum())
    return {
        "forecast": fc,
        "mape": mape,
        "bias": bias,
        "coverage": coverage,
        "n_months": int(mask.sum()),
    }
