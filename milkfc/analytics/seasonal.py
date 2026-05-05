"""月度乳量分布分析：每場每年每月乳量、季節指數、全國基準。"""
import pandas as pd
import numpy as np
from typing import Optional

def compute_monthly_distribution(
    df: pd.DataFrame,
    year_range: Optional[tuple] = None,
) -> pd.DataFrame:
    """每場每年每月的總乳量、平均日乳量、活躍頭數。

    Args:
        df: DHI 紀錄
        year_range: (start_year, end_year) inclusive；None 為全部
    Returns:
        columns = [farm_id, year, month, total_milk_kg, avg_daily_milk,
                   n_active_cows, season_index]
    """
    d = df.copy()
    d = d[d["sample_date"].notna() & d["milk_kg"].notna()]
    d["year"] = d["sample_date"].dt.year
    d["month"] = d["sample_date"].dt.month

    if year_range is not None:
        y0, y1 = year_range
        d = d[(d["year"] >= y0) & (d["year"] <= y1)]

    # 每頭牛每月只算一筆（DHI 通常一個月測一次），用日乳量平均代表該月
    monthly_per_cow = d.groupby(
        ["farm_id","year","month","cow_id"]
    )["milk_kg"].mean().reset_index()
    monthly_per_cow.rename(columns={"milk_kg": "avg_daily_milk"}, inplace=True)

    # 場別月度：總月乳量 = 每頭日均 × 30、活躍頭數
    farm_monthly = monthly_per_cow.groupby(
        ["farm_id","year","month"]
    ).agg(
        avg_daily_milk=("avg_daily_milk","mean"),
        n_active_cows=("cow_id","nunique"),
    ).reset_index()
    farm_monthly["total_milk_kg"] = (
        farm_monthly["avg_daily_milk"] *
        farm_monthly["n_active_cows"] * 30
    )

    # 季節指數 = 該月乳量 / 該場該年 12 月平均
    yearly = farm_monthly.groupby(["farm_id","year"])["total_milk_kg"].transform("mean")
    farm_monthly["season_index"] = farm_monthly["total_milk_kg"] / yearly

    return farm_monthly


def compute_national_monthly(
    farm_monthly: pd.DataFrame,
    weighted: bool = True,
) -> pd.DataFrame:
    """加總成全國視圖。

    Args:
        weighted=True: 直接合併（保留場間規模×產量的交互作用，產業實際樣貌）
        weighted=False: 拆成「平均每頭日乳量 × 平均場頭數 × 場數」
                        每場一票，反映「典型場大小化的全國」
    Returns:
        columns = [year, month, total_milk_kg, n_farms, n_cows,
                   avg_per_farm, season_index, scope]
    """
    if weighted:
        # 加權 = 直接加總全部場（保留場別大小 × 產量相關性）
        nat = farm_monthly.groupby(["year","month"]).agg(
            total_milk_kg=("total_milk_kg","sum"),
            n_farms=("farm_id","nunique"),
            n_cows=("n_active_cows","sum"),
        ).reset_index()
        nat["avg_per_farm"] = nat["total_milk_kg"] / nat["n_farms"]
        nat["scope"] = "weighted"
    else:
        # 簡單 = 平均日乳量(每頭) × 平均頭數 × 場數
        # 與加權差在「拆掉場大小與產量的協變」
        nat = farm_monthly.groupby(["year","month"]).agg(
            mean_daily_milk_per_cow=("avg_daily_milk","mean"),
            mean_n_cows=("n_active_cows","mean"),
            n_farms=("farm_id","nunique"),
            n_cows=("n_active_cows","sum"),
        ).reset_index()
        # 全國估算 = 平均產量 × 平均場頭數 × 場數 × 30 天
        nat["total_milk_kg"] = (
            nat["mean_daily_milk_per_cow"] *
            nat["mean_n_cows"] *
            nat["n_farms"] * 30
        )
        nat["avg_per_farm"] = (
            nat["mean_daily_milk_per_cow"] *
            nat["mean_n_cows"] * 30
        )
        nat["scope"] = "simple"

    yearly = nat.groupby("year")["total_milk_kg"].transform("mean")
    nat["season_index"] = nat["total_milk_kg"] / yearly
    return nat


def summary_stats(monthly: pd.DataFrame) -> dict:
    """計算季節指標：夏季衰退、變異係數、峰月。"""
    by_month = monthly.groupby("month")["total_milk_kg"].mean()
    if len(by_month) < 12:
        return {}
    peak_month = int(by_month.idxmax())
    peak_v = float(by_month.max())
    trough_month = int(by_month.idxmin())
    trough_v = float(by_month.min())
    summer_drop_pct = float((trough_v - peak_v) / peak_v * 100)
    cv = float(by_month.std() / by_month.mean() * 100)
    return {
        "peak_month": peak_month,
        "trough_month": trough_month,
        "peak_value": peak_v,
        "trough_value": trough_v,
        "summer_drop_pct": summer_drop_pct,
        "cv_pct": cv,
    }
