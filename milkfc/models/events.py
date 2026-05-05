"""分娩事件流、配種成功率、後備轉換率"""
import pandas as pd
import numpy as np
from .. import config

def build_calvings(d: pd.DataFrame) -> pd.DataFrame:
    """從 last_calving_date 變動點重建分娩事件流。"""
    s = d.sort_values(["cow_id","sample_date"]).copy()
    s["prev_lcd"] = s.groupby("cow_id")["last_calving_date"].shift(1)
    c = s[s["last_calving_date"].notna() &
          (s["last_calving_date"] != s["prev_lcd"])][
        ["cow_id","last_calving_date","parity"]].copy()
    return c.rename(columns={"last_calving_date":"calving_date"}
                    ).drop_duplicates(["cow_id","calving_date"])

def fit_breeding_success(d_train: pd.DataFrame, calvings: pd.DataFrame) -> dict:
    """估算各胎次群配種成功率。"""
    breed = d_train[d_train["last_breeding_date"].notna()].drop_duplicates(
        ["cow_id","last_breeding_date"]).copy()
    if len(breed) == 0:
        return {"by_parity": {}, "overall": 0.45}

    breed["expected_calving"] = breed["last_breeding_date"] + pd.Timedelta(
        days=config.GESTATION_DAYS)

    cal_by_cow = calvings.groupby("cow_id")["calving_date"].apply(list).to_dict()
    def is_pregnant(row):
        cs = cal_by_cow.get(row["cow_id"], [])
        return any(abs((c - row["expected_calving"]).days) <= 30 for c in cs)

    breed["pregnant"] = breed.apply(is_pregnant, axis=1).astype(float)
    breed["parity_grp"] = breed["parity"].fillna(0).clip(upper=4).astype(int)
    by_parity = breed.groupby("parity_grp")["pregnant"].mean().to_dict()
    overall = float(breed["pregnant"].mean())
    return {"by_parity": {int(k): float(v) for k, v in by_parity.items()},
            "overall": overall}

def fit_heifer_pipeline(d_train: pd.DataFrame, calvings: pd.DataFrame) -> dict:
    """估算後備母牛入場率（兩種估法取較大值）。"""
    if len(calvings) == 0:
        return {"conv_rate": 0.4, "recent_first_rate": 0.0,
                "estimated_per_month": 0.0}

    calvings = calvings.copy()
    calvings["yyyymm"] = calvings["calving_date"].dt.to_period("M")
    first = calvings[calvings["parity"]==1]

    by_month = calvings.groupby("yyyymm").size()
    by_month_first = first.groupby("yyyymm").size()

    pairs = [(mo, by_month.get(mo,0), by_month_first.get(mo+24,0))
             for mo in by_month.index]
    df_pairs = pd.DataFrame(pairs, columns=["mo","calvings","first_24m"])
    if df_pairs["calvings"].sum() > 0:
        conv_rate = df_pairs["first_24m"].sum() / df_pairs["calvings"].sum()
    else:
        conv_rate = 0.4

    train_end = d_train["sample_date"].max()
    cutoff = train_end - pd.Timedelta(days=365)
    recent = first[(first["calving_date"] > cutoff) &
                   (first["calving_date"] <= train_end)]
    recent_first_rate = len(recent) / 12.0

    # 24m 前分娩 × 轉換率 vs 最近 12m 平均，取較大值
    cutoff_24m = train_end - pd.Timedelta(days=24*30)
    recent_calvings = calvings[(calvings["calving_date"] >= cutoff_24m) &
                               (calvings["calving_date"] <= train_end)]
    rate_a = len(recent_calvings) * conv_rate / 24
    estimated = max(rate_a, recent_first_rate)

    return {
        "conv_rate": float(conv_rate),
        "recent_first_rate": float(recent_first_rate),
        "estimated_per_month": float(estimated),
    }

def estimate_growth_rate(d_train: pd.DataFrame) -> dict:
    """估算月活躍頭數成長率（解決系統性偏低）。
    用最近 12 個月 vs 之前 12 個月的活躍頭數比較。
    """
    train_end = d_train["sample_date"].max()
    if pd.isna(train_end):
        return {"growth_pct_yoy": 0.0, "monthly_active": {}}

    d_train = d_train.copy()
    d_train["yyyymm"] = d_train["sample_date"].dt.to_period("M")
    monthly_active = d_train.groupby("yyyymm")["cow_id"].nunique()

    # 最近 12 月 vs 之前 12 月平均
    if len(monthly_active) < 24:
        return {"growth_pct_yoy": 0.0,
                "monthly_active": {str(k): int(v) for k, v in monthly_active.items()}}

    last_12 = monthly_active.iloc[-12:].mean()
    prev_12 = monthly_active.iloc[-24:-12].mean()
    if prev_12 > 0:
        growth = (last_12 - prev_12) / prev_12
    else:
        growth = 0.0
    # 限制在 ±30% 防離群影響
    growth = float(max(min(growth, 0.30), -0.30))
    return {
        "growth_pct_yoy": growth,
        "monthly_active": {str(k): int(v) for k, v in monthly_active.items()},
    }
