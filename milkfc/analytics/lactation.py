"""泌乳曲線分析：場別 / 全國 / 個體牛 三層粒度。"""
import pandas as pd
import numpy as np
from typing import Optional, Tuple
from ..models.lactation import fit_wood_loglinear, wood

DIM_BIN_DAYS = 7  # 每 7 天一格
DIM_BINS = list(range(0, 365, DIM_BIN_DAYS))  # 0, 7, 14, ..., 357


def _prep_data(df: pd.DataFrame, year_range: Optional[Tuple[int,int]] = None):
    d = df.copy()
    d = d[d["sample_date"].notna() & d["dim"].between(1, 365) &
          d["milk_kg"].between(1, 80) & d["parity"].between(1, 10)]
    d["parity_grp"] = d["parity"].clip(upper=4).astype(int)
    d["year"] = d["sample_date"].dt.year
    if year_range is not None:
        y0, y1 = year_range
        d = d[(d["year"] >= y0) & (d["year"] <= y1)]
    d["dim_bin"] = (d["dim"] // DIM_BIN_DAYS) * DIM_BIN_DAYS
    return d


def compute_lactation_curves(
    df: pd.DataFrame,
    year_range: Optional[Tuple[int,int]] = None,
) -> dict:
    """場別、加權全國、簡單全國三種粒度的泌乳曲線統計。

    Returns dict with:
      'farm_curves': df [farm_id, parity_grp, dim_bin, p10, p25, p50, p75, p90, n]
      'national_weighted': df [parity_grp, dim_bin, ...]   依紀錄筆數加權
      'national_simple':   df [parity_grp, dim_bin, ...]   各場簡單平均
      'farm_kpis': df [farm_id, parity_grp, peak_kg, peak_dim, persistency_pct, total_305d]
      'national_weighted_kpis': dict by parity_grp
      'national_simple_kpis':   dict by parity_grp
    """
    d = _prep_data(df, year_range)
    if len(d) == 0:
        return {"farm_curves": pd.DataFrame(),
                "national_weighted": pd.DataFrame(),
                "national_simple": pd.DataFrame(),
                "farm_kpis": pd.DataFrame()}

    # === 場別曲線 ===
    farm_curves = d.groupby(["farm_id","parity_grp","dim_bin"])["milk_kg"].agg([
        ("p10", lambda x: float(np.percentile(x, 10))),
        ("p25", lambda x: float(np.percentile(x, 25))),
        ("p50", lambda x: float(np.percentile(x, 50))),
        ("p75", lambda x: float(np.percentile(x, 75))),
        ("p90", lambda x: float(np.percentile(x, 90))),
        ("n", "size"),
    ]).reset_index()

    # === 加權全國曲線（直接合併所有紀錄）===
    nat_w = d.groupby(["parity_grp","dim_bin"])["milk_kg"].agg([
        ("p10", lambda x: float(np.percentile(x, 10))),
        ("p25", lambda x: float(np.percentile(x, 25))),
        ("p50", lambda x: float(np.percentile(x, 50))),
        ("p75", lambda x: float(np.percentile(x, 75))),
        ("p90", lambda x: float(np.percentile(x, 90))),
        ("n", "size"),
    ]).reset_index()
    nat_w["scope"] = "weighted"

    # === 簡單全國曲線（場間 P50 取平均）===
    nat_s = farm_curves.groupby(["parity_grp","dim_bin"]).agg(
        p10=("p10","mean"), p25=("p25","mean"),
        p50=("p50","mean"), p75=("p75","mean"), p90=("p90","mean"),
        n=("n","sum"),
    ).reset_index()
    nat_s["scope"] = "simple"

    # === KPI: 每場 + 全國 ===
    farm_kpis = _curves_to_kpis(farm_curves, group_cols=["farm_id","parity_grp"])
    nat_w_kpis = _curves_to_kpis(nat_w, group_cols=["parity_grp"])
    nat_s_kpis = _curves_to_kpis(nat_s, group_cols=["parity_grp"])

    return {
        "farm_curves": farm_curves,
        "national_weighted": nat_w,
        "national_simple": nat_s,
        "farm_kpis": farm_kpis,
        "national_weighted_kpis": nat_w_kpis,
        "national_simple_kpis": nat_s_kpis,
    }


def _curves_to_kpis(curves_df: pd.DataFrame, group_cols: list) -> pd.DataFrame:
    """從曲線資料萃取 KPI: peak、peak_DIM、persistency、305-day total"""
    rows = []
    for keys, sub in curves_df.groupby(group_cols):
        sub = sub.sort_values("dim_bin")
        if len(sub) == 0: continue
        peak_idx = sub["p50"].idxmax()
        peak_kg = float(sub.loc[peak_idx,"p50"])
        peak_dim = int(sub.loc[peak_idx,"dim_bin"])
        # 305 天總乳量 ≈ Σ p50 * 7（每個 bin 7 天）
        sub_305 = sub[sub["dim_bin"] < 305]
        total_305 = float(sub_305["p50"].sum() * DIM_BIN_DAYS)
        # 持續力 = (305 天平均日乳量 / 峰值) × 100
        avg_d = total_305 / 305 if total_305 > 0 else 0
        persist = avg_d / peak_kg * 100 if peak_kg > 0 else 0
        if isinstance(keys, tuple):
            row = dict(zip(group_cols, keys))
        else:
            row = {group_cols[0]: keys}
        row.update({
            "peak_kg": peak_kg,
            "peak_dim": peak_dim,
            "persistency_pct": float(persist),
            "total_305d_kg": total_305,
        })
        rows.append(row)
    return pd.DataFrame(rows)


def compute_individual_curve(
    df: pd.DataFrame,
    farm_id: str,
    cow_id: str,
    year_range: Optional[Tuple[int,int]] = None,
) -> dict:
    """個體牛的測乳點 + Wood 擬合曲線（如果可擬合）。

    Returns:
        {'points': df [dim, milk_kg, parity, sample_date],
         'fit': {parity_grp: (a,b,c)} or None,
         'kpis': dict}
    """
    d = _prep_data(df, year_range)
    cow_d = d[(d["farm_id"]==farm_id) & (d["cow_id"]==cow_id)].copy()
    cow_d = cow_d[["dim","milk_kg","parity","parity_grp","sample_date"]]

    fit = {}
    for pg in cow_d["parity_grp"].unique():
        sub = cow_d[cow_d["parity_grp"]==pg]
        if len(sub) >= 3:
            params = fit_wood_loglinear(sub["dim"].values, sub["milk_kg"].values)
            if params is not None:
                fit[int(pg)] = params

    # KPIs
    kpis = {
        "n_test_records": len(cow_d),
        "max_milk_kg": float(cow_d["milk_kg"].max()) if len(cow_d) else None,
        "avg_milk_kg": float(cow_d["milk_kg"].mean()) if len(cow_d) else None,
        "parities_observed": sorted(cow_d["parity"].dropna().unique().tolist()),
    }
    return {"points": cow_d, "fit": fit, "kpis": kpis}


def list_farm_cows(df: pd.DataFrame, farm_id: str, min_records: int = 3) -> pd.DataFrame:
    """列出某場有足夠紀錄的牛，給下拉選單用。"""
    d = df[(df["farm_id"]==farm_id) & df["milk_kg"].notna()]
    cows = d.groupby("cow_id").agg(
        n_records=("milk_kg","size"),
        n_parities=("parity","nunique"),
        max_milk=("milk_kg","max"),
    ).reset_index()
    cows = cows[cows["n_records"] >= min_records]
    return cows.sort_values("n_records", ascending=False)
