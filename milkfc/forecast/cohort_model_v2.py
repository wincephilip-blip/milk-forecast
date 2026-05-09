"""Cohort 結構模型 v2（漸進改善版）。

相對於 v1 (cohort_model.forecast_cohort_simple)、本模組支援更細粒度之資料
與 nowcast 流程，但全部以 flag 控制；所有 flag 預設值皆等同 v1，因此
forecast_cohort_v2(...) 在預設參數下與 forecast_cohort_simple(...) 產生
相同的 annual_total_tons。

支援的 flag：
    n_projection ∈ {'annual', 'quarterly'}
        'annual'    : v1 行為，年度資料線性外推（預設）
        'quarterly' : phase 2，季度資料投影到目標年並平均

    q_projection ∈ {'annual_linear', 'monthly_stl'}
        'annual_linear': v1 行為，年度單頭產量線性外推（預設）
        'monthly_stl'  : phase 1，月度 DHI 量用 statsmodels.STL 分解

    r_window ∈ {'fixed_5y', 'adaptive'}
        'fixed_5y' : v1 行為，最近 5 年 OLS 線性外推（預設）
        'adaptive' : phase 4，視 r 之最近波動度決定窗口長度

    as_of_date  : 決策日（ISO 字串）；用於 nowcast，限制可用資料範圍
    nowcast_mode ∈ {'auto', 'off', 'force'}
        'auto'  : 偵測 as_of_date 對應 target_year 已有幾季資料、自動使用（預設）
        'off'   : 強制純外推、忽略 target_year 任何已知資料
        'force' : 要求至少 1 季 nowcast；無資料則 raise

當前實作狀態：
    phase 0 (本檔起始)：所有 flag 之非預設分支皆 NotImplementedError
        但 default-flag 路徑已完全可運行、與 v1 結果一致。
    phase 1：實作 q_projection='monthly_stl'
    phase 2：實作 n_projection='quarterly'（含 as_of_date / nowcast_mode）
    phase 4：實作 r_window='adaptive'
"""
from __future__ import annotations

import logging
import json
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from .cohort_model import (
    _seasonal_pattern,
    _linear_extrapolate,
    _get_dhi_yearly_yield,
    _get_official_cows_history,
    _compute_productivity_ratio,
    _extrapolate_productivity_ratio,
)

log = logging.getLogger("milkfc.cohort.v2")


# =====================================================================
# v2-specific projection hooks (phase 1+ 會逐步實作)
# =====================================================================

def _load_monthly_q(history_min_year: int = 2018,
                      history_max_year: Optional[int] = None,
                      cache_path: Optional[Path] = None) -> pd.Series:
    """載入月度 DHI Q 序列（kg/day per record）。

    Returns: pd.Series indexed by yyyymm (string), value = kg/day
    """
    from .. import config
    cache_path = cache_path or (config.SNAPSHOT_DIR / "_dhi_monthly_yield.json")
    if not cache_path.exists():
        raise FileNotFoundError(
            f"月度 Q cache 不存在: {cache_path}。請先執行 "
            f"reports/_build_monthly_q_cache.py")

    raw = json.loads(cache_path.read_text())
    rows = []
    for ym, v in raw.items():
        y = int(ym[:4])
        if y < history_min_year:
            continue
        if history_max_year is not None and y > history_max_year:
            continue
        rows.append((ym, v["Q_kg_per_day"]))

    rows.sort()
    return pd.Series(
        [v for _, v in rows],
        index=[ym for ym, _ in rows],
        dtype=float,
    )


def _project_q_monthly_stl(target_year: int,
                             history_min_year: int = 2018,
                             history_max_year: Optional[int] = None,
                             dhi_cache_path: Optional[Path] = None,
                             min_history_months: int = 36) -> float:
    """[phase 1] 用月度 DHI Q 序列做 STL 分解 + 趨勢線性外推、平均得年度 Q。

    流程：
        1. 取月度 Q 序列（2018-01 ~ history_max_year-12）
        2. STL(period=12, robust=True) 分解 → trend + seasonal + resid
        3. 對 trend 做 OLS 線性外推到 target_year 全年 12 個月
        4. seasonal 採最近 12 個月的季節項（迴圈套用至目標年）
        5. predicted_monthly_Q = trend_extrap + seasonal_recent
        6. annual_Q = mean(12 個月)

    Returns: 年度 Q（kg/day per record，與 v1 annual 同單位）
    """
    from statsmodels.tsa.seasonal import STL

    series = _load_monthly_q(
        history_min_year, history_max_year, dhi_cache_path)

    if len(series) < min_history_months:
        log.warning(f"  Q monthly STL: 月度資料不足 ({len(series)} < "
                     f"{min_history_months})、退回 annual_linear")
        # 退回 v1 annual
        yearly = _get_dhi_yearly_yield(
            history_min_year, history_max_year, dhi_cache_path)
        years = sorted(yearly.keys())
        if len(years) >= 2:
            return _linear_extrapolate(
                years, [yearly[y] for y in years], target_year)
        return float(series.iloc[-1]) if len(series) else 0.0

    # STL 分解
    y = series.values
    stl = STL(y, period=12, robust=True).fit()
    trend = stl.trend
    seasonal = stl.seasonal

    # 線性外推 trend 到 target_year 之 12 個月
    n = len(y)
    last_ym = pd.Period(series.index[-1], freq="M")
    last_year = last_ym.year
    last_month = last_ym.month

    # 外推月數 = 從 last_ym 之下個月起、到 target_year 12 月止
    target_end = pd.Period(f"{target_year}-12", freq="M")
    h = (target_end - last_ym).n  # 月差
    if h <= 0:
        # target_year 已包含於歷史中（純粹回測時可能發生）
        # 取 target_year 12 個月平均當答案
        target_months = [f"{target_year}-{m:02d}" for m in range(1, 13)]
        vals = [series.loc[m] for m in target_months if m in series.index]
        return float(np.mean(vals)) if vals else float(series.iloc[-1])

    xs = np.arange(n)
    slope, intercept = np.polyfit(xs, trend, 1)
    future_x = np.arange(n, n + h)
    trend_future = slope * future_x + intercept

    # 季節項：取最近一個完整 12 個月之季節成分（循環套用）
    seasonal_cycle = seasonal[-12:]  # 月份順序＝最後 12 個月
    # 對齊：last_ym 之下個月對應 last_month+1
    seasonal_future = np.array([
        seasonal_cycle[(last_month - 1 + i + 1) % 12]
        for i in range(h)
    ])

    monthly_Q_future = trend_future + seasonal_future

    # 取 target_year 之 12 個月（位於 future 序列的後 12 個位置）
    target_year_Q = monthly_Q_future[-12:]
    annual_Q = float(np.mean(target_year_Q))

    log.info(f"  Q monthly STL: history {series.index[0]} ~ {series.index[-1]} "
              f"({n} m), 外推 {h} m, target year monthly mean = {annual_Q:.3f} kg/d")

    return annual_Q


def _available_nowcast_quarters(target_year: int,
                                   as_of_date: Optional[str]) -> list:
    """根據 as_of_date 判斷 target_year 之 Q1/Q2/Q3 是否已公告。

    保守估計各季公告滯後：
        Q1 (Jan-Mar) 公告 ≈ 5 月初
        Q2 (Apr-Jun) 公告 ≈ 8 月初
        Q3 (Jul-Sep) 公告 ≈ 11 月初
        Q4 不單獨公告

    Returns: list of int (1, 2, 3)，表 target_year 之哪幾季可用為 nowcast 錨點
    """
    if as_of_date is None:
        return []
    as_of = pd.Timestamp(as_of_date)
    publish_dates = {
        1: pd.Timestamp(f"{target_year}-05-01"),
        2: pd.Timestamp(f"{target_year}-08-01"),
        3: pd.Timestamp(f"{target_year}-11-01"),
    }
    return [q for q, d in publish_dates.items() if as_of >= d]


def _project_n_quarterly(target_year: int,
                            history_max_year: Optional[int] = None,
                            as_of_date: Optional[str] = None,
                            n_recent_quarters: int = 12,
                            include_annual_anchor: bool = True,
                            nowcast_mode: str = 'auto') -> float:
    """[phase 2] 用季度 N 序列投影目標年。

    流程：
        1. 取季報 N 序列（2019Q1+），含 quarter→decimal_year 轉換
        2. （可選）加入年報年度 N 當作年末錨點（提高擬合穩定性）
        3. 取最近 n_recent_quarters 季資料、做 OLS 線性回歸
        4. 預測目標年 4 個季度 N（Q1=y+0.125, Q2=y+0.375, Q3=y+0.625, Q4=y+0.875）
        5. 平均得年度 N

    nowcast 邏輯（暫未啟用、phase 3 處理 as_of_date）：
        若 as_of_date 對應 target_year 已有 Q1/Q2/Q3 資料，
        則該季用真值取代外推值。

    Returns: 年度 N
    """
    from ..data.quarterly_inventory import QUARTERLY_INVENTORY, quarter_to_decimal_year
    from ..data.official_inventory import OFFICIAL_DAIRY_INVENTORY

    # 1) 判斷 nowcast 哪幾季可用
    nowcast_quarters = _available_nowcast_quarters(target_year, as_of_date)
    if nowcast_mode == 'off':
        nowcast_quarters = []
    elif nowcast_mode == 'force' and not nowcast_quarters:
        raise RuntimeError(
            f"nowcast_mode='force' 但 as_of_date={as_of_date} 對 "
            f"target_year={target_year} 無可用季度資料")

    # 2) 構造季度 (decimal_year, n_milking_cows) 點
    points = []
    for qid, info in QUARTERLY_INVENTORY.items():
        y, q = int(qid[:4]), int(qid[5:6])
        # 限制：歷史 ≤ history_max_year，OR target_year 但季度在 nowcast_quarters 中
        if y > target_year:
            continue
        if y == target_year and q not in nowcast_quarters:
            continue
        if y < target_year and history_max_year is not None and y > history_max_year:
            continue
        n = info.get("n_milking_cows", 0)
        if n > 0:
            points.append((quarter_to_decimal_year(qid), float(n)))

    # 3) 加入年報年度錨點
    if include_annual_anchor:
        for y, info in OFFICIAL_DAIRY_INVENTORY.items():
            if y >= target_year:
                continue
            if history_max_year is not None and y > history_max_year:
                continue
            n = info.get("n_milking_cows", 0)
            if n > 0:
                points.append((y + 0.5, float(n)))

    if len(points) < 4:
        log.warning(f"  N quarterly: 點數不足 ({len(points)})、退回 annual_linear")
        # fallback
        from .cohort_model import _get_official_cows_history
        cows = _get_official_cows_history(history_max_year=history_max_year)
        years = sorted(cows.keys())
        if len(years) >= 2:
            return _linear_extrapolate(
                years, [cows[y] for y in years], target_year)
        return points[-1][1] if points else 0.0

    # 4) 排序 + 取最近 n_recent_quarters 點做迴歸
    points.sort()
    recent = points[-n_recent_quarters:] if len(points) > n_recent_quarters else points

    xs = np.array([p[0] for p in recent])
    ys = np.array([p[1] for p in recent])
    slope, intercept = np.polyfit(xs, ys, 1)

    # 5) 預測目標年 4 季 N，nowcast 季度直接用真值
    quarter_mids = [target_year + (q - 0.5) * 0.25 for q in [1, 2, 3, 4]]
    pred_quarters = []
    for q, qm in zip([1, 2, 3, 4], quarter_mids):
        if q in nowcast_quarters:
            qid = f"{target_year}Q{q}"
            actual = QUARTERLY_INVENTORY.get(qid, {}).get("n_milking_cows", 0)
            if actual > 0:
                pred_quarters.append(float(actual))
                continue
        # 外推
        pred_quarters.append(float(slope * qm + intercept))
    annual_N = float(np.mean(pred_quarters))

    log.info(f"  N quarterly: {len(recent)} 點 ({recent[0][0]:.3f}~{recent[-1][0]:.3f}), "
              f"nowcast {nowcast_quarters or 'none'}, "
              f"annual N target={target_year} → {annual_N:,.0f} 頭")

    return annual_N


def _r_adaptive_window(ratios: dict, target_year: int,
                          method: str = 'ensemble') -> float:
    """[phase 4] r_t 自適應外推。

    method 選項：
        'ensemble' (預設): 5 年 OLS 與 3 年算術平均的 50/50 平均
            理由：純 OLS 對 r 之最近反彈反應慢、純 mean 對長期趨勢反應慢；
            兩者平均能在 r 穩定下降期保留 OLS 之趨勢能力，又在 r 反彈期
            被 mean 拉回較合理水準。
        'variance_switch': 看最近 5 年 r 之 CV，CV > 3% 用 3 年 mean、否則 5 年 OLS
        'regime_change': 看最近兩年 r 變化方向，反向 → 3 年 mean、同向 → 5 年 OLS

    歷史驗證（2021-2024）r MAPE：
        5yr OLS (v1)      : 2.48%
        ensemble          : 2.31%
        variance_switch   : ~2.4%
    """
    if not ratios:
        return 1.0
    sorted_yrs = sorted(ratios.keys())

    # 5 年 OLS（v1 行為）
    p_ols = _extrapolate_productivity_ratio(
        ratios, target_year, n_recent=5, floor=1.0)

    # 3 年算術平均
    last3 = sorted_yrs[-3:] if len(sorted_yrs) >= 3 else sorted_yrs
    p_mean = float(np.mean([ratios[y] for y in last3]))

    if method == 'ensemble':
        # 50/50 平均，並套同樣的 floor=1.0、ceiling 保護
        ratio_vals = [ratios[y] for y in last3]
        ceiling = max(ratio_vals) * 1.1
        return max(1.0, min(ceiling, 0.5 * p_ols + 0.5 * p_mean))

    elif method == 'variance_switch':
        recent5 = sorted_yrs[-5:]
        vals = np.array([ratios[y] for y in recent5])
        cv = vals.std() / vals.mean() if vals.mean() > 0 else 0
        if cv > 0.03:  # > 3%
            return max(1.0, p_mean)
        return p_ols

    elif method == 'regime_change':
        if len(sorted_yrs) >= 3:
            d1 = ratios[sorted_yrs[-1]] - ratios[sorted_yrs[-2]]
            d2 = ratios[sorted_yrs[-2]] - ratios[sorted_yrs[-3]]
            if d1 * d2 < 0:  # 反向（regime change）
                return max(1.0, p_mean)
        return p_ols

    else:
        raise ValueError(f"unknown adaptive method: {method}")


# =====================================================================
# Main entry
# =====================================================================

def forecast_cohort_v2(
    target_year: int,
    horizon_months: int = 12,
    history_min_year: int = 2018,
    history_max_year: Optional[int] = None,
    dhi_cache_path: Optional[Path] = None,
    apply_productivity_correction: bool = True,
    # ----- v2-specific flags -----
    n_projection: str = 'annual',          # 'annual' / 'quarterly'
    q_projection: str = 'annual_linear',   # 'annual_linear' / 'monthly_stl'
    r_window: str = 'fixed_5y',            # 'fixed_5y' / 'adaptive'
    as_of_date: Optional[str] = None,
    nowcast_mode: str = 'auto',            # 'auto' / 'off' / 'force'
) -> dict:
    """Cohort v2 預測。預設參數下與 forecast_cohort_simple 結果一致。

    回傳 dict 鍵與 v1 相同，另加：
        'v2_config': {n_projection, q_projection, r_window,
                       as_of_date, nowcast_mode_actual}
    """
    # 1) 取年度資料（共同基礎）
    yield_history = _get_dhi_yearly_yield(
        history_min_year, history_max_year, dhi_cache_path)
    cows_history = _get_official_cows_history(
        history_min_year, history_max_year)

    common_years = sorted(set(yield_history.keys()) & set(cows_history.keys()))
    if len(common_years) < 3:
        log.warning("  Cohort v2: 共同歷史年份 < 3、跳過")
        return {"model": "cohort_v2", "success": False,
                "error": "insufficient history", "forecast": []}

    yields_kg = [yield_history[y] for y in common_years]
    cows = [cows_history[y] for y in common_years]

    # 2) Q 投影 ------------------------------------------------------
    if q_projection == 'annual_linear':
        pred_yield = _linear_extrapolate(common_years, yields_kg, target_year)
    elif q_projection == 'monthly_stl':
        pred_yield = _project_q_monthly_stl(
            target_year=target_year,
            history_min_year=history_min_year,
            history_max_year=history_max_year,
            dhi_cache_path=dhi_cache_path)
    else:
        raise ValueError(f"unknown q_projection: {q_projection}")

    # 3) N 投影 ------------------------------------------------------
    if n_projection == 'annual':
        pred_cows = _linear_extrapolate(common_years, cows, target_year)
    elif n_projection == 'quarterly':
        pred_cows = _project_n_quarterly(
            target_year=target_year,
            history_max_year=history_max_year,
            as_of_date=as_of_date,
            nowcast_mode=nowcast_mode)
    else:
        raise ValueError(f"unknown n_projection: {n_projection}")

    # 4) r_t 投影 ----------------------------------------------------
    ratio_history = _compute_productivity_ratio(
        yield_history,
        history_min_year=2015,
        history_max_year=history_max_year)

    if apply_productivity_correction and ratio_history:
        if r_window == 'fixed_5y':
            ratio_target = _extrapolate_productivity_ratio(
                ratio_history, target_year, n_recent=5, floor=1.0)
        elif r_window == 'adaptive':
            ratio_target = _r_adaptive_window(ratio_history, target_year)
        else:
            raise ValueError(f"unknown r_window: {r_window}")
    else:
        ratio_target = 1.0

    # 5) 年產量 -----------------------------------------------------
    annual_tons_raw = pred_cows * pred_yield * 305 / 1000
    annual_tons = (annual_tons_raw / ratio_target
                    if ratio_target > 0 else annual_tons_raw)

    # 6) leave-one-out cross-validation in-sample MAPE ---------------
    # （與 v1 相同邏輯；不受 v2 flag 影響）
    errors = []
    if len(common_years) >= 4:
        for i, y in enumerate(common_years[2:], start=2):
            train_y = common_years[:i]
            train_yi = [yield_history[yy] for yy in train_y]
            train_cow = [cows_history[yy] for yy in train_y]
            loo_yield = _linear_extrapolate(train_y, train_yi, y)
            loo_cows = _linear_extrapolate(train_y, train_cow, y)
            train_ratios = {yy: ratio_history[yy] for yy in train_y
                              if yy in ratio_history}
            if apply_productivity_correction and train_ratios:
                loo_ratio = _extrapolate_productivity_ratio(
                    train_ratios, y, n_recent=5, floor=1.0)
            else:
                loo_ratio = 1.0
            loo_pred = loo_cows * loo_yield * 305 / 1000
            if loo_ratio > 0:
                loo_pred /= loo_ratio
            true_ratio = (ratio_history.get(y, 1.0)
                            if apply_productivity_correction else 1.0)
            true_pred = cows_history[y] * yield_history[y] * 305 / 1000
            if true_ratio > 0:
                true_pred /= true_ratio
            err = abs(loo_pred - true_pred) / true_pred * 100
            errors.append(err)
    in_sample_mape = float(np.mean(errors)) if errors else 5.0

    # 7) 月度分配（v1 邏輯：固定季節比例後處理）---------------------
    seasonal = _seasonal_pattern()
    err_pct = max(in_sample_mape, 2.0) / 100
    forecast = []
    start = pd.Timestamp(f"{target_year}-01-01")
    for i in range(horizon_months):
        m = start + pd.DateOffset(months=i)
        month_idx = (m.month - 1) % 12
        p50 = annual_tons * seasonal[month_idx] * 1000
        p10 = p50 * (1 - 1.28 * err_pct)
        p90 = p50 * (1 + 1.28 * err_pct)
        forecast.append({
            "yyyymm": m.strftime("%Y-%m"),
            "p50": p50,
            "p10": p10,
            "p90": p90,
        })

    correction_str = (
        f"× (1/{ratio_target:.3f}={1/ratio_target:.3f}) productivity 校正"
        if apply_productivity_correction else "（無校正）")
    log.info(f"  Cohort v2 預測 {target_year}：產乳牛 {pred_cows:,.0f} 頭 × "
              f"單頭日產乳 {pred_yield:.1f} kg × 305 天 "
              f"{correction_str} = {annual_tons:,.0f} 公噸"
              f"（in-sample MAPE {in_sample_mape:.1f}%）"
              f" [n={n_projection}, q={q_projection}, r={r_window}]")

    return {
        "model": "cohort_v2",
        "success": True,
        "in_sample_mape": in_sample_mape,
        "forecast": forecast,
        "annual_total_tons": annual_tons,
        "annual_total_tons_raw": annual_tons_raw,
        "predicted_cows": pred_cows,
        "predicted_daily_yield_kg": pred_yield,
        "productivity_ratio_target": ratio_target,
        "productivity_ratio_history": ratio_history,
        "productivity_correction_applied": apply_productivity_correction,
        "history_years": common_years,
        "v2_config": {
            "n_projection": n_projection,
            "q_projection": q_projection,
            "r_window": r_window,
            "as_of_date": as_of_date,
            "nowcast_mode_actual": nowcast_mode,
        },
    }
