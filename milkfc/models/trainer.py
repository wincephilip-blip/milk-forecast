"""整合所有模型訓練"""
import pandas as pd
from .lactation import fit_lactation_curves
from .events import (build_calvings, fit_breeding_success,
                     fit_heifer_pipeline, estimate_growth_rate)
from .sexed_semen import compute_farm_sexed_rate, adjust_heifer_rate

def train_models(d_train: pd.DataFrame,
                  farm_county: str = None,
                  thi_smooth_alpha: float = 0.3) -> dict:
    """訓練 Phase 2.5 完整模型（含成長率 + 性控偵測 + 氣象 THI 先驗）。

    Args:
        farm_county: 該場縣市（用來查 THI 30 年常態做季節乘子平滑）
        thi_smooth_alpha: THI 先驗強度（0=不用、0.3=輕度收斂、1=完全 THI）
    """
    if len(d_train) < 200:
        raise ValueError(f"訓練資料不足: {len(d_train)} 筆")

    lact = fit_lactation_curves(d_train)
    calvings = build_calvings(d_train)
    breed = fit_breeding_success(d_train, calvings)
    heifer = fit_heifer_pipeline(d_train, calvings)
    growth = estimate_growth_rate(d_train)

    # === 用 THI 30 年常態平滑季節乘子（如果有 county 對應）===
    thi_applied = False
    if farm_county and thi_smooth_alpha > 0:
        try:
            from ..data.weather import smooth_seasonal_with_thi
            old_seasonal = dict(lact["seasonal"])
            lact["seasonal"] = smooth_seasonal_with_thi(
                lact["seasonal"], farm_county, alpha=thi_smooth_alpha)
            thi_applied = (old_seasonal != lact["seasonal"])
        except Exception as e:
            import logging
            logging.getLogger("milkfc.trainer").warning(
                f"THI smoothing failed for {farm_county}: {e}")

    # 性控精液偵測（單場處理時取唯一場的比例）
    sexed_rates = compute_farm_sexed_rate(d_train)
    if sexed_rates:
        # 假設 d_train 是單場資料
        sexed_rate = list(sexed_rates.values())[0] if len(sexed_rates) == 1 \
                      else sum(sexed_rates.values()) / len(sexed_rates)
    else:
        sexed_rate = 0.0

    # 用性控比例調整後備母牛入場率
    base_heifer = heifer["estimated_per_month"]
    adj_heifer = adjust_heifer_rate(base_heifer, sexed_rate)

    return {
        "pop_curves": lact["pop_curves"],
        "seasonal": lact["seasonal"],
        "cow_factor": lact["cow_factor"],
        "preg_rate": breed["by_parity"],
        "preg_rate_overall": breed["overall"],
        "conv_rate": heifer["conv_rate"],
        "recent_first_rate": heifer["recent_first_rate"],
        "expected_heifers_per_month": adj_heifer,             # ← 用性控調整後
        "expected_heifers_per_month_base": base_heifer,       # 原值（debug 用）
        "_thi_applied": thi_applied,
        "sexed_semen_rate": float(sexed_rate),
        "growth_pct_yoy": growth["growth_pct_yoy"],
        "monthly_active": growth["monthly_active"],
        "_training_subset": lact["training_subset"],
        "_calvings": calvings,
    }
