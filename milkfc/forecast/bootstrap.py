"""參數 Bootstrap - 把 Wood 曲線與其他參數的估計誤差納入區間."""
import pandas as pd
import numpy as np
from ..models.lactation import fit_wood_loglinear, wood
from .simulator import simulate_cow_future
from ..models.events import build_calvings
from .. import config

def bootstrap_curves(training_subset: pd.DataFrame, n_boot: int = None, seed: int = 42):
    """對訓練資料 bootstrap 重抽，每次重新擬合 Wood 參數。
    回傳: list of pop_curves dicts。
    """
    n_boot = n_boot or config.N_BOOTSTRAP
    rng = np.random.default_rng(seed)
    all_curves = []

    for _ in range(n_boot):
        # 對每個 parity_grp 內 bootstrap
        boot_pop = {}
        for pg in config.PARITY_GROUPS:
            sub = training_subset[training_subset["parity_grp"]==pg]
            if len(sub) < 30:
                continue
            idx = rng.integers(0, len(sub), size=len(sub))
            samp = sub.iloc[idx]
            params = fit_wood_loglinear(samp["dim"].values, samp["milk_kg"].values)
            if params is not None:
                boot_pop[pg] = params

        # 缺漏補齊
        for pg in config.PARITY_GROUPS:
            if pg not in boot_pop:
                if not boot_pop:
                    break
                nearest = sorted(boot_pop.keys(), key=lambda x: abs(x-pg))[0]
                boot_pop[pg] = boot_pop[nearest]

        if boot_pop:
            all_curves.append(boot_pop)
    return all_curves


def forecast_with_bootstrap(d_train, ref_date, horizon_months, models, n_sim=None, seed=42):
    """生產級預測：每次模擬都從 bootstrap 池抽一組曲線參數。
    結合事件隨機性 + 參數估計不確定性 → 更誠實的 P10-P90 區間。
    """
    n_sim = n_sim or config.N_SIMULATIONS
    rng = np.random.default_rng(seed)
    horizon_days = horizon_months * 30 + 30

    # 活躍牛
    recent = d_train[d_train["sample_date"] >
                     ref_date - pd.Timedelta(days=config.ACTIVE_LOOKBACK_DAYS)]
    latest = recent.sort_values("sample_date").groupby("cow_id").tail(1)

    # 後備母牛入場率（成長率校正）
    growth = models["growth_pct_yoy"]
    base_heifer = models["expected_heifers_per_month"]
    # 若場在成長中，後備入場率也應略增
    adjusted_heifer = base_heifer * (1 + max(growth, 0) * 0.5)

    # Bootstrap 曲線池
    boot_curves = bootstrap_curves(models["_training_subset"], seed=seed)
    if not boot_curves:
        boot_curves = [models["pop_curves"]]

    # 預先計算每日的 timestamp 陣列與季節乘子陣列
    days_arr = ref_date + pd.to_timedelta(np.arange(horizon_days), unit="D")
    months = days_arr.month
    seasonal_arr = np.array([models["seasonal"].get(int(m), 1.0) for m in months])

    # 把活躍牛轉成 dict list 預先處理，避免 iterrows 開銷
    cow_records = latest.to_dict("records")

    sim_results = []
    for s in range(n_sim):
        # 每次模擬抽一組曲線（參數不確定性）
        sampled_pc = boot_curves[rng.integers(0, len(boot_curves))]
        boot_models = {**models, "pop_curves": sampled_pc}

        sim_yields = {}
        for cow in cow_records:
            ys = simulate_cow_future(cow, ref_date, horizon_days,
                                      boot_models, rng,
                                      seasonal_arr=seasonal_arr,
                                      days_arr=days_arr)
            for dt, y in ys.items():
                sim_yields[dt] = sim_yields.get(dt, 0) + y

        # 後備母牛貢獻（向量化版）
        pc1 = sampled_pc[1]
        for m_idx in range(horizon_months):
            n_new = rng.poisson(adjusted_heifer)
            if n_new == 0:
                continue
            for _ in range(n_new):
                cal_offset = m_idx * 30 + rng.integers(0, 30)
                if cal_offset >= horizon_days:
                    continue
                # 該頭新牛從 cal_offset 開始 305 天的曲線
                d_offsets = np.arange(305)
                date_offsets = cal_offset + d_offsets
                mask = date_offsets < horizon_days
                if not mask.any():
                    continue
                d_offsets_v = d_offsets[mask]
                date_offsets_v = date_offsets[mask]
                t = np.maximum(d_offsets_v, 1)
                y_arr = pc1[0] * (t**pc1[1]) * np.exp(-pc1[2]*t)
                y_arr = y_arr * seasonal_arr[date_offsets_v]
                y_arr = np.maximum(y_arr, 0)
                for idx, y_val in zip(date_offsets_v, y_arr):
                    if y_val > 0:
                        dt = days_arr[idx]
                        sim_yields[dt] = sim_yields.get(dt, 0) + y_val
        sim_results.append(sim_yields)

    # 加總成月度 + 取分位數
    monthly_sims = {}
    for s in sim_results:
        df_s = pd.Series(s)
        df_s.index = pd.to_datetime(df_s.index)
        ms = df_s.groupby(df_s.index.to_period("M")).sum()
        for p, v in ms.items():
            monthly_sims.setdefault(p, []).append(v)

    rows = []
    for p, vals in sorted(monthly_sims.items()):
        rows.append({
            "yyyymm": str(p),
            "p10": float(np.percentile(vals, 10)),
            "p25": float(np.percentile(vals, 25)),
            "p50": float(np.percentile(vals, 50)),
            "p75": float(np.percentile(vals, 75)),
            "p90": float(np.percentile(vals, 90)),
            "mean": float(np.mean(vals)),
            "std": float(np.std(vals)),
        })

    meta = {
        "n_active_cows": len(latest),
        "expected_heifers_per_month_adj": float(adjusted_heifer),
        "growth_pct_yoy": growth,
        "n_bootstrap_curves": len(boot_curves),
    }
    return pd.DataFrame(rows), meta
