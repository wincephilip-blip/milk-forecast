"""場別四維度分類器：規模 / 場齡 / 趨勢 / 區域。"""
import pandas as pd
import numpy as np
from typing import Optional
from .data import load_farm_metadata


def compute_farm_dhi_stats(df: pd.DataFrame) -> pd.DataFrame:
    """從 DHI 資料算每場的規模與趨勢統計。"""
    fy = df.groupby(["farm_id","year","month"]).agg(
        n_cows=("cow_id","nunique"),
    ).reset_index()
    farm_stats = fy.groupby("farm_id").agg(
        n_cows_median=("n_cows","median"),
        n_cows_min=("n_cows","min"),
        n_cows_max=("n_cows","max"),
        n_records=("n_cows","size"),
    ).reset_index()

    # YoY 成長：最近 12 個月活躍頭數平均 vs 之前 12 個月
    fy_sorted = fy.sort_values(["farm_id","year","month"])
    growth = []
    for fid, sub in fy_sorted.groupby("farm_id"):
        if len(sub) < 24:
            growth.append({"farm_id": fid, "yoy_growth_pct": 0.0})
            continue
        last_12 = sub.iloc[-12:]["n_cows"].mean()
        prev_12 = sub.iloc[-24:-12]["n_cows"].mean()
        if prev_12 > 0:
            g = (last_12 - prev_12) / prev_12 * 100
        else:
            g = 0.0
        growth.append({"farm_id": fid, "yoy_growth_pct": float(g)})
    growth_df = pd.DataFrame(growth)

    return farm_stats.merge(growth_df, on="farm_id")


def classify_farms(df: pd.DataFrame,
                   farm_meta: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """對所有場做四維度分類。

    Returns:
        columns = [farm_id, scale, vintage, trend, macro_region, segment, ...]
    """
    if farm_meta is None:
        farm_meta = load_farm_metadata()

    stats = compute_farm_dhi_stats(df)

    # 規模分類
    def scale_of(n):
        if n < 100: return "small"
        if n < 250: return "medium"
        if n < 500: return "large"
        return "xlarge"
    stats["scale"] = stats["n_cows_median"].apply(scale_of)

    # 趨勢分類
    def trend_of(g):
        if g > 5: return "expanding"
        if g < -5: return "shrinking"
        return "stable"
    stats["trend"] = stats["yoy_growth_pct"].apply(trend_of)

    # 合併 farm_meta 的場齡與區域
    if len(farm_meta) > 0:
        merged = stats.merge(
            farm_meta[["farm_id","age_years","macro_region","region_name",
                       "advisor_id"]],
            on="farm_id", how="left",
        )
    else:
        merged = stats.copy()
        merged["age_years"] = None
        merged["macro_region"] = "未知"
        merged["region_name"] = "未知"
        merged["advisor_id"] = None

    def vintage_of(age):
        if pd.isna(age): return "unknown"
        if age < 10: return "new"
        if age < 25: return "mid"
        return "old"
    merged["vintage"] = merged["age_years"].apply(vintage_of)
    merged["macro_region"] = merged["macro_region"].fillna("未知")

    # 組合 segment 標籤
    merged["segment"] = (
        merged["macro_region"].astype(str) + "-" +
        merged["scale"] + "-" + merged["trend"]
    )

    return merged


def compute_segment_priors(df: pd.DataFrame, classification: pd.DataFrame) -> dict:
    """每個 segment 算平均參數，給小場資料不足時使用。

    Returns:
        {segment_label: {pop_curves_means, seasonal_means, etc.}}
    """
    from .models import train_models

    priors = {}
    for seg, sub in classification.groupby("segment"):
        farm_ids = sub["farm_id"].tolist()
        if len(farm_ids) < 3:
            continue

        # 在這個 segment 的場合併資料訓練（取得平均參數）
        seg_df = df[df["farm_id"].isin(farm_ids)]
        if len(seg_df) < 200:
            continue

        try:
            seg_models = train_models(seg_df)
            priors[seg] = {
                "pop_curves": seg_models["pop_curves"],
                "seasonal": seg_models["seasonal"],
                "preg_rate": seg_models["preg_rate"],
                "preg_rate_overall": seg_models["preg_rate_overall"],
                "conv_rate": seg_models["conv_rate"],
                "n_farms": len(farm_ids),
            }
        except Exception:
            continue

    return priors


def apply_segment_prior(farm_models: dict,
                         segment_label: str,
                         priors: dict,
                         shrinkage: float = 0.3) -> dict:
    """對單場模型參數做向 segment prior 收斂的 shrinkage。

    Args:
        farm_models: 該場原本訓練的模型
        segment_label: 該場所屬 segment
        priors: 全部 segment priors
        shrinkage: 向 prior 收斂的比例 (0=不收斂, 1=完全用 prior)
    """
    if segment_label not in priors:
        return farm_models
    prior = priors[segment_label]
    new = dict(farm_models)

    # 對 Wood 曲線做加權平均收斂
    new_pop = {}
    for pg, params in farm_models["pop_curves"].items():
        if pg in prior["pop_curves"]:
            p_prior = prior["pop_curves"][pg]
            new_pop[pg] = tuple(
                (1-shrinkage) * params[i] + shrinkage * p_prior[i]
                for i in range(3)
            )
        else:
            new_pop[pg] = params
    new["pop_curves"] = new_pop

    # 季節乘子收斂
    new_seasonal = {}
    for m, v in farm_models["seasonal"].items():
        prior_v = prior["seasonal"].get(m, 1.0)
        new_seasonal[m] = (1-shrinkage) * v + shrinkage * prior_v
    new["seasonal"] = new_seasonal

    # 配種成功率收斂（只對有 prior 的胎次）
    new_preg = {}
    for pg, v in farm_models["preg_rate"].items():
        prior_v = prior["preg_rate"].get(pg, prior["preg_rate_overall"])
        new_preg[pg] = (1-shrinkage) * v + shrinkage * prior_v
    new["preg_rate"] = new_preg

    return new
