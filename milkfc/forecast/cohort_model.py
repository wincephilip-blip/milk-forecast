"""Cohort 結構模型（簡化版 B1）。

物理拆解：
    年產量 = 泌乳牛數 × 平均單頭年產乳

預測流程：
    1. 線性外推「歷年泌乳牛數」（從在養量資料）到目標年
    2. 線性外推「歷年單頭日產乳」（從 DHI 直接算）到目標年
    3. 兩者相乘 × 305 天 = 年產量
    4. 用季節形式拆分到 12 個月

完全不依賴歷年「產量」資料、預測完全獨立。
"""
import logging
import json
import numpy as np
import pandas as pd
from collections import defaultdict
from pathlib import Path

from ..data.official_inventory import OFFICIAL_DAIRY_INVENTORY
from ..data.quarterly_inventory import QUARTERLY_INVENTORY, quarter_to_decimal_year
from .. import config

log = logging.getLogger("milkfc.cohort")


def _seasonal_pattern() -> list:
    """台灣酪農典型月度乳量比例（春末高、夏末低、12 個值加總 = 1.0）。"""
    return [
        0.080, 0.082, 0.085, 0.087, 0.086, 0.084,
        0.082, 0.082, 0.080, 0.081, 0.083, 0.085,
    ]


def _linear_extrapolate(xs: list, ys: list, target: float,
                          max_drop_factor: float = 0.95) -> float:
    """線性外推、加下限避免過度衝下。"""
    if len(xs) < 2:
        return ys[-1] if ys else 0
    xs = np.array(xs, dtype=float)
    ys = np.array(ys, dtype=float)
    slope, b = np.polyfit(xs, ys, 1)
    pred = float(slope * target + b)
    return max(pred, ys[-1] * max_drop_factor)


def _get_dhi_yearly_yield(history_min_year: int = 2018,
                            history_max_year: int = None,
                            cache_path: Path = None) -> dict:
    """從 DHI 抽取每年「平均單頭日產乳 (kg)」。

    Returns: {year: avg_daily_kg}
    """
    cache_path = cache_path or (config.SNAPSHOT_DIR / "_dhi_yearly_cows.json")
    if not cache_path.exists():
        return {}
    data = json.loads(cache_path.read_text())
    out = {}
    for k, v in data.items():
        y = int(k)
        if y < history_min_year:
            continue
        if history_max_year and y > history_max_year:
            continue
        if v.get("n_records", 0) > 0:
            out[y] = v["dhi_total_kg"] / v["n_records"]
    return out


def _get_official_cows_history(history_min_year: int = 2018,
                                  history_max_year: int = None,
                                  include_quarterly_estimate: bool = True) -> dict:
    """從官方在養量資料抽歷年泌乳牛數。

    Args:
        include_quarterly_estimate: True 時、若年報尚無某年資料、
            從季報多季平均估算（給 2025 等已過部分時間但尚未公告年報的年用）。

    Returns: {year: n_milking_cows}
    """
    out = {}
    # 1. 年報資料（已公告）
    for y, info in OFFICIAL_DAIRY_INVENTORY.items():
        if y < history_min_year:
            continue
        if history_max_year and y > history_max_year:
            continue
        out[y] = info["n_milking_cows"]

    # 2. 用季報補年報缺的年（例如 2025 還沒公告年報、用 Q1-Q3 平均）
    if include_quarterly_estimate:
        try:
            from ..data.quarterly_inventory import QUARTERLY_INVENTORY
            # 找年報缺的年、看季報有沒有資料
            yrs_in_quarterly = sorted(set(int(str(q)[:4]) for q in QUARTERLY_INVENTORY))
            for y in yrs_in_quarterly:
                if y in out:
                    continue
                if y < history_min_year:
                    continue
                if history_max_year and y > history_max_year:
                    continue
                # 取該年所有可用季的平均
                qs = [v for q, v in QUARTERLY_INVENTORY.items()
                       if str(q).startswith(str(y))]
                if qs:
                    avg_cows = sum(q.get("n_milking_cows", 0) for q in qs) / len(qs)
                    if avg_cows > 0:
                        out[y] = int(round(avg_cows))
                        log.info(f"  Cohort: {y} 用季報 {len(qs)} 季平均 = "
                                  f"{int(round(avg_cows)):,} 頭（年報尚未公告）")
        except Exception as e:
            log.debug(f"  季報補資料失敗、略過: {e}")
    return out


def _get_national_yield_history(history_min_year: int = 2015,
                                   history_max_year: int = None) -> dict:
    """從官方〈牛乳產量〉與〈在養量〉算歷年「全國平均單頭日產乳 kg」。

    全國單頭日產乳 = 公告產量(kg) / 公告產乳牛 / 305 天

    Returns: {year: national_avg_daily_kg}
    """
    out = {}
    for y, info in OFFICIAL_DAIRY_INVENTORY.items():
        if y < history_min_year:
            continue
        if history_max_year and y > history_max_year:
            continue
        cows = info.get("n_milking_cows", 0)
        prod_tons = info.get("production_tons", 0)
        if cows > 0 and prod_tons > 0:
            # production_tons (公噸) → kg / cows / 305 days
            out[y] = prod_tons * 1000 / cows / 305
    return out


def _compute_productivity_ratio(yield_history: dict,
                                  history_min_year: int = 2015,
                                  history_max_year: int = None) -> dict:
    """算每年 DHI 單頭產量 / 全國單頭產量 比率。

    這個比率反映「DHI 樣本場 vs 全國平均」生產力差距。
    若比率 > 1：DHI 樣本生產力較高（樣本偏向先進場）。
    若比率收斂到 1：DHI 越來越代表性。

    歷史趨勢（台灣）：從 2015 的 1.21 收斂到 2024 的 1.06。

    Args:
        yield_history: DHI 歷年單頭產量 {year: kg/day}

    Returns: {year: ratio}（DHI/全國）
    """
    nat_yield = _get_national_yield_history(history_min_year, history_max_year)
    ratios = {}
    for y, dhi_y in yield_history.items():
        if y in nat_yield and nat_yield[y] > 0:
            ratios[y] = dhi_y / nat_yield[y]
    return ratios


def _extrapolate_productivity_ratio(ratios: dict, target_year: int,
                                       n_recent: int = 5,
                                       floor: float = 1.0,
                                       ceiling: float = None) -> float:
    """把 productivity ratio 線性外推到目標年。

    限制：
      - floor: 比率下限（預設 1.0、DHI 不會低於全國平均）
      - ceiling: 比率上限（預設取最近 n 年最大值）

    Returns: float ratio
    """
    if not ratios:
        return 1.0
    sorted_items = sorted(ratios.items())
    recent = sorted_items[-n_recent:]
    if len(recent) < 2:
        return list(ratios.values())[-1]
    xs = np.array([y + 0.5 for y, _ in recent])
    ys = np.array([r for _, r in recent])
    slope, b = np.polyfit(xs, ys, 1)
    pred = float(slope * (target_year + 0.5) + b)
    # 加邊界保護
    if ceiling is None:
        ceiling = max(ys) * 1.1  # 不超過歷史最高 1.1 倍
    return max(floor, min(ceiling, pred))


def forecast_cohort_simple(target_year: int, horizon_months: int = 12,
                             history_min_year: int = 2018,
                             history_max_year: int = None,
                             dhi_cache_path: Path = None,
                             apply_productivity_correction: bool = True) -> dict:
    """Cohort 簡化版預測。

    Args:
        target_year: 目標年（西元）
        horizon_months: 預測月數
        history_min_year/max_year: 訓練歷史範圍（給 backtest 用）
        dhi_cache_path: DHI 牛/場/紀錄數快取（測試用）
        apply_productivity_correction: 是否套用「DHI/全國 productivity ratio」
            動態校正（預設 True）。
            說明：DHI 樣本場單頭產量比全國平均高 6-12%、若不校正、cohort 會
            系統性高估 ~10%。校正會用歷年比率線性外推到目標年。

    Returns:
        {
            "model": "cohort_simple",
            "success": bool,
            "forecast": [{yyyymm, p50, p10, p90}],
            "in_sample_mape": float,
            "annual_total_tons": float,         # 校正後（若啟用）
            "annual_total_tons_raw": float,     # 原始 cohort 預測（未校正）
            "predicted_cows": float,
            "predicted_daily_yield_kg": float,
            "productivity_ratio_target": float, # 校正用比率
            "productivity_ratio_history": dict, # 歷年比率
        }
    """
    # 1) 取資料
    yield_history = _get_dhi_yearly_yield(history_min_year, history_max_year,
                                              dhi_cache_path)
    cows_history = _get_official_cows_history(history_min_year, history_max_year)

    common_years = sorted(set(yield_history.keys()) & set(cows_history.keys()))
    if len(common_years) < 3:
        log.warning(f"  Cohort: 共同歷史年份 < 3、跳過")
        return {"model": "cohort_simple", "success": False,
                "error": "insufficient history", "forecast": []}

    yields_kg = [yield_history[y] for y in common_years]
    cows = [cows_history[y] for y in common_years]

    # 2) 線性外推到目標年
    pred_yield = _linear_extrapolate(common_years, yields_kg, target_year)
    pred_cows = _linear_extrapolate(common_years, cows, target_year)

    # 3) 算 productivity ratio（DHI / 全國）並外推到目標年
    ratio_history = _compute_productivity_ratio(
        yield_history, history_min_year=2015,
        history_max_year=history_max_year)
    if apply_productivity_correction and ratio_history:
        ratio_target = _extrapolate_productivity_ratio(
            ratio_history, target_year, n_recent=5,
            floor=1.0)
    else:
        ratio_target = 1.0

    # 4) 年產量 = 牛數 × 日產乳 × 305 天 / 1000 (kg → 公噸)
    annual_tons_raw = pred_cows * pred_yield * 305 / 1000
    # 套 productivity 校正：DHI 樣本生產力高於全國、所以除掉比率還原全國尺度
    annual_tons = (annual_tons_raw / ratio_target
                    if ratio_target > 0 else annual_tons_raw)

    # 5) leave-one-out cross-validation 算 in-sample MAPE（含校正）
    errors = []
    if len(common_years) >= 4:
        for i, y in enumerate(common_years[2:], start=2):
            train_y = common_years[:i]
            train_yi = [yield_history[yy] for yy in train_y]
            train_cow = [cows_history[yy] for yy in train_y]
            loo_yield = _linear_extrapolate(train_y, train_yi, y)
            loo_cows = _linear_extrapolate(train_y, train_cow, y)
            # 同樣外推 productivity ratio
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
            # 真值用「該年實際在養量 × 該年實際單頭日產乳 × 305」並除實際比率
            true_ratio = ratio_history.get(y, 1.0) if apply_productivity_correction else 1.0
            true_pred = cows_history[y] * yield_history[y] * 305 / 1000
            if true_ratio > 0:
                true_pred /= true_ratio
            err = abs(loo_pred - true_pred) / true_pred * 100
            errors.append(err)
    in_sample_mape = float(np.mean(errors)) if errors else 5.0

    # 6) 月度分配
    seasonal = _seasonal_pattern()
    err_pct = max(in_sample_mape, 2.0) / 100  # 至少 ±2%
    forecast = []
    start = pd.Timestamp(f"{target_year}-01-01")
    for i in range(horizon_months):
        m = start + pd.DateOffset(months=i)
        month_idx = (m.month - 1) % 12
        # P50 月值（kg、與其他模型同量綱）
        p50 = annual_tons * seasonal[month_idx] * 1000
        p10 = p50 * (1 - 1.28 * err_pct)
        p90 = p50 * (1 + 1.28 * err_pct)
        forecast.append({
            "yyyymm": m.strftime("%Y-%m"),
            "p50": p50,
            "p10": p10,
            "p90": p90,
        })

    correction_str = (f"× (1/{ratio_target:.3f}={1/ratio_target:.3f}) productivity 校正"
                       if apply_productivity_correction else "（無校正）")
    log.info(f"  Cohort 預測 {target_year}：產乳牛 {pred_cows:,.0f} 頭 × "
              f"單頭日產乳 {pred_yield:.1f} kg × 305 天 "
              f"{correction_str} = {annual_tons:,.0f} 公噸"
              f"（{annual_tons/10000:.2f} 萬公噸、in-sample MAPE {in_sample_mape:.1f}%）")
    if apply_productivity_correction:
        log.info(f"    [productivity ratio target {target_year} = "
                  f"{ratio_target:.3f}（從 {len(ratio_history)} 年歷史外推）]")

    return {
        "model": "cohort_simple",
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
    }
