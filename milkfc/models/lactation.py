"""Wood 乳期曲線擬合"""
import numpy as np
import pandas as pd
from .. import config

def fit_wood_loglinear(t, y):
    """y = a*t^b*exp(-c*t) → log(y) = log(a) + b*log(t) - c*t"""
    valid = (t > 0) & (y > 0)
    t, y = np.asarray(t)[valid], np.asarray(y)[valid]
    if len(t) < 5:
        return None
    X = np.column_stack([np.ones_like(t), np.log(t), -t])
    coef, *_ = np.linalg.lstsq(X, np.log(y), rcond=None)
    return float(np.exp(coef[0])), float(coef[1]), float(coef[2])

def wood(t, a, b, c):
    t = np.maximum(t, 1)
    return a * (t**b) * np.exp(-c*t)

def fit_lactation_curves(d_train: pd.DataFrame) -> dict:
    """擬合各胎次群 Wood 曲線 + 季節乘子 + 個體因子。

    回傳:
      pop_curves: {parity_grp: (a,b,c)}
      seasonal: {month: multiplier}
      cow_factor: {cow_id: factor}
      training_data: 用於 bootstrap 的訓練資料
    """
    lo_dim, hi_dim = config.WOOD_DIM_RANGE
    lo_milk, hi_milk = config.MILK_RANGE
    lo_p, hi_p = config.PARITY_RANGE

    m = (d_train["dim"].between(lo_dim, hi_dim) &
         d_train["milk_kg"].between(lo_milk, hi_milk) &
         d_train["parity"].between(lo_p, hi_p) &
         d_train["sample_date"].notna())
    d = d_train[m].copy()
    d["parity_grp"] = d["parity"].clip(upper=4).astype(int)
    d["sample_month"] = d["sample_date"].dt.month

    pop_curves = {}
    for pg in sorted(d["parity_grp"].unique()):
        sub = d[d["parity_grp"]==pg]
        if len(sub) < 30:
            continue
        params = fit_wood_loglinear(sub["dim"].values, sub["milk_kg"].values)
        if params is not None:
            pop_curves[int(pg)] = params

    if not pop_curves:
        raise ValueError("無足夠資料擬合 Wood 曲線")

    # 補滿 1-4 胎次群（缺漏時用最近的補）
    for pg in config.PARITY_GROUPS:
        if pg not in pop_curves:
            nearest = sorted(pop_curves.keys(), key=lambda x: abs(x-pg))[0]
            pop_curves[pg] = pop_curves[nearest]

    # 季節乘子（顯式轉 float 避免新 pandas dtype 問題）
    d["pred_pop"] = d.apply(
        lambda r: wood(r["dim"], *pop_curves[r["parity_grp"]]), axis=1).astype(float)
    d["resid"] = (d["milk_kg"].astype(float) / d["pred_pop"]).astype(float)
    seasonal = d.groupby("sample_month")["resid"].median().to_dict()
    for mm in range(1, 13):
        seasonal.setdefault(mm, 1.0)

    # 個體因子（n<3 時收縮到 1.0）
    d["pred_seasoned"] = d.apply(
        lambda r: r["pred_pop"] * seasonal.get(r["sample_month"], 1.0), axis=1).astype(float)
    d["cow_resid"] = (d["milk_kg"].astype(float) / d["pred_seasoned"]).astype(float)
    cf = d.groupby("cow_id")["cow_resid"].agg(["median","count"])
    cf.columns = ["factor","n"]
    cf["factor_smooth"] = np.where(
        cf["n"] >= 3, cf["factor"],
        (cf["factor"]*cf["n"] + 1.0*3) / (cf["n"] + 3))

    return {
        "pop_curves": pop_curves,
        "seasonal": seasonal,
        "cow_factor": cf["factor_smooth"].to_dict(),
        "training_subset": d[["dim","milk_kg","parity_grp","sample_month","cow_id"]],
    }
