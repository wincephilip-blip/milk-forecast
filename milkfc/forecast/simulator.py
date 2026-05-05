"""逐牛蒙地卡羅模擬（向量化版本，比原版快 10-50 倍）"""
import pandas as pd
import numpy as np
from ..models.lactation import wood
from .. import config


def simulate_cow_future_vec(cow_state, ref_date, horizon_days, models, rng,
                             seasonal_arr=None, days_arr=None):
    """向量化版：一頭牛未來 horizon_days 的逐日乳量。
    與原 simulate_cow_future 同邏輯，但用 numpy 陣列取代逐日 pd.Timedelta。

    Args:
        seasonal_arr: 預先計算的每日季節乘子（length=horizon_days）
        days_arr: 預先計算的每日 pd.Timestamp 陣列
    Returns:
        dict {pd.Timestamp: float}
    """
    parity = int(cow_state["parity"]) if pd.notna(cow_state.get("parity")) else 1
    parity_grp = min(max(parity, 1), 4)
    cow_factor = models["cow_factor"].get(cow_state["cow_id"], 1.0)
    pc = models["pop_curves"][parity_grp]

    lcd = cow_state.get("last_calving_date")
    lbd = cow_state.get("last_breeding_date")

    # 預期下次分娩
    next_calving = None
    if pd.notna(lbd):
        days_since_breed = (ref_date - lbd).days
        if 0 <= days_since_breed <= 290 and (pd.isna(lcd) or lbd > lcd):
            preg_p = models["preg_rate"].get(parity_grp, models["preg_rate_overall"])
            if rng.random() < preg_p:
                next_calving = lbd + pd.Timedelta(days=config.GESTATION_DAYS)

    # 向量化計算：每日距離 ref_date 的天數
    if days_arr is None:
        days_arr = ref_date + pd.to_timedelta(np.arange(horizon_days), unit="D")

    # 決定每日對應的 lcd 與 pc
    yields_arr = np.zeros(horizon_days)

    if next_calving is not None:
        nc_offset = (next_calving - ref_date).days
        # 階段 1: 還沒到下次分娩 (cur_lcd = lcd)
        # 階段 2: 已到下次分娩 (cur_lcd = next_calving, 胎次 +1)
        # 階段 3: 分娩前 60 天乾乳期 (yield = 0)

        # 階段 2 的胎次曲線
        next_pg = min(parity_grp + 1, 4)
        next_pc = models["pop_curves"][next_pg]

        if pd.notna(lcd):
            # DIM 在分娩前
            offsets = np.arange(horizon_days)
            dim_pre = (offsets + (ref_date - lcd).days)
            mask_pre = (dim_pre >= 0) & (dim_pre <= 365) & (offsets < nc_offset)
            # 排除乾乳期
            dry_off_start = nc_offset - config.DRY_OFF_DAYS
            mask_pre = mask_pre & (offsets < dry_off_start)
            if mask_pre.any():
                t = np.maximum(dim_pre[mask_pre], 1)
                yields_arr[mask_pre] = pc[0] * (t**pc[1]) * np.exp(-pc[2]*t)

        # 階段 2: cur_date >= next_calving
        offsets_post = np.arange(horizon_days) - nc_offset
        mask_post = (offsets_post >= 0) & (offsets_post <= 365)
        if mask_post.any():
            t = np.maximum(offsets_post[mask_post], 1)
            yields_arr[mask_post] = next_pc[0] * (t**next_pc[1]) * np.exp(-next_pc[2]*t)
    else:
        # 無下次分娩，整段都用 lcd 對應的 Wood 曲線
        if pd.notna(lcd):
            offsets = np.arange(horizon_days)
            dim = offsets + (ref_date - lcd).days
            mask = (dim >= 0) & (dim <= 365)
            if mask.any():
                t = np.maximum(dim[mask], 1)
                yields_arr[mask] = pc[0] * (t**pc[1]) * np.exp(-pc[2]*t)

    # 季節乘子
    if seasonal_arr is None:
        months = days_arr.month if hasattr(days_arr, "month") else \
                 np.array([d.month for d in days_arr])
        seasonal = models["seasonal"]
        seasonal_arr = np.array([seasonal.get(int(m), 1.0) for m in months])

    yields_arr = yields_arr * seasonal_arr * cow_factor
    yields_arr = np.maximum(yields_arr, 0.0)

    # 只回傳非零的（節省記憶體 + 後續加總更快）
    nonzero = yields_arr > 0
    if not nonzero.any():
        return {}
    return dict(zip(days_arr[nonzero], yields_arr[nonzero]))


# 保留向後相容的舊函式名（接受可選 seasonal_arr / days_arr）
def simulate_cow_future(cow_state, ref_date, horizon_days, models, rng,
                          seasonal_arr=None, days_arr=None):
    return simulate_cow_future_vec(cow_state, ref_date, horizon_days, models, rng,
                                    seasonal_arr=seasonal_arr, days_arr=days_arr)
