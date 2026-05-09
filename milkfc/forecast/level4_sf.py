"""Level 4 SF post-processor。

跑完 timeseries snapshot 後、用 Level 4 邏輯重算「校正後全國尺度」：

  L4 SF[Y] = 估計官方場數[Y] / 估計 DHI 場數[Y]

  估計官方場數 = 季報 + 年報線性外推（用最新可得資料）
  估計 DHI 場數 = DHI 歷年場數線性外推

把預測結果（含每個模型、ensemble、歷史）都乘上 L4 SF（按該月份所屬年份對應）、
產出新的 calibrated_l4 區段、給儀表板用。
"""
import logging
import numpy as np

log = logging.getLogger("milkfc.level4")


SF_METHODS = ("farms", "cows", "mixed")


def _extrapolate_dhi_value(dhi_panel: dict, target_year: int,
                              field: str, n_recent: int = 5) -> float:
    """從 dhi_panel 取出某欄位（n_farms / n_cows）、線性外推到目標年。"""
    pts = sorted([(yy, v.get(field, 0)) for yy, v in dhi_panel.items()
                   if v.get(field, 0) > 0])
    if len(pts) < 2:
        return 0.0
    recent = pts[-n_recent:]
    xs = np.array([p[0] + 0.5 for p in recent])
    ys = np.array([p[1] for p in recent])
    slope, b = np.polyfit(xs, ys, 1)
    est = float(slope * (target_year + 0.5) + b)
    # 防止外推往下衝過頭
    return max(est, recent[-1][1] * 0.95), float(slope)


def compute_l4_sf_by_year(years: list, dhi_panel: dict,
                            quarterly: dict = None,
                            annual: dict = None,
                            method: str = "farms") -> dict:
    """為每個年度算 L4 SF。

    Args:
        years: 想算 SF 的年份清單
        dhi_panel: {year: {n_farms, n_cows, ...}}（DHI 歷史）
        quarterly: 季報（默認 QUARTERLY_INVENTORY）
        annual: 年報（默認 OFFICIAL_DAIRY_INVENTORY）
        method: SF 計算方法
            - "farms"：官方場數 / DHI 場數（預設、原行為）
            - "cows"：官方產乳牛 / DHI 產乳牛
            - "mixed"：farms 與 cows 的 50/50 加權平均

    Returns:
        {year: {sf, official_farms, dhi_farms, official_cows, dhi_cows,
                method, source}}
    """
    from ..data.quarterly_inventory import (
        estimate_official_for_year, quarter_to_decimal_year,
        QUARTERLY_INVENTORY)
    from ..data.official_inventory import OFFICIAL_DAIRY_INVENTORY
    if method not in SF_METHODS:
        raise ValueError(f"method must be one of {SF_METHODS}、got {method!r}")

    quarterly = quarterly or QUARTERLY_INVENTORY
    annual = annual or OFFICIAL_DAIRY_INVENTORY

    out = {}
    for y in years:
        # 1) 估官方場數 + 牛口
        try:
            est_off = estimate_official_for_year(y, quarterly=quarterly,
                                                    annual=annual)
        except Exception as e:
            log.warning(f"  Year {y} 估官方失敗: {e}")
            continue

        official_farms = est_off.get("n_dairy_farms", 0)
        official_cows = est_off.get("n_milking_cows", 0)

        # 2) 估 DHI 場數 + 牛口（兩者都算、後面依 method 取用）
        est_dhi_farms, slope_farms = _extrapolate_dhi_value(
            dhi_panel, y, "n_farms")
        est_dhi_cows, slope_cows = _extrapolate_dhi_value(
            dhi_panel, y, "n_cows")

        if est_dhi_farms <= 0:
            continue

        # 3) 依 method 算 SF
        sf_farms = official_farms / est_dhi_farms if est_dhi_farms else 0
        sf_cows = (official_cows / est_dhi_cows
                    if (est_dhi_cows > 0 and official_cows > 0) else 0)

        if method == "farms":
            sf = sf_farms
        elif method == "cows":
            if sf_cows <= 0:
                # fallback：缺牛口資料時退回 farms
                log.warning(f"  Year {y} 缺牛口資料、SF 退回場數比")
                sf = sf_farms
            else:
                sf = sf_cows
        elif method == "mixed":
            if sf_cows <= 0:
                sf = sf_farms
            else:
                sf = 0.5 * sf_farms + 0.5 * sf_cows

        out[y] = {
            "year": y,
            "sf": float(sf),
            "sf_farms": float(sf_farms),
            "sf_cows": float(sf_cows) if sf_cows > 0 else None,
            "method": method,
            "official_farms": float(official_farms),
            "official_cows": float(official_cows),
            "dhi_farms": float(est_dhi_farms),
            "dhi_cows": float(est_dhi_cows) if est_dhi_cows > 0 else None,
            "source": est_off.get("source", "extrapolation"),
            "dhi_slope": float(slope_farms),
        }
    return out


def apply_l4_calibration(results: dict, dhi_panel: dict = None,
                           holdout: dict = None,
                           with_cohort: bool = False,
                           target_year: int = None,
                           sf_method: str = "farms") -> dict:
    """產生 calibrated_l4 區段並掛到全國 results 上。

    Args:
        results: ts_results.json 解開後的 dict
        dhi_panel: DHI 歷年場數（為 None 自動載快取）
        holdout: holdout backtest 結果（含 by_model_mape 與 bias）
        with_cohort: True 時加入 cohort 結構模型（不需 SF、直接 national）
        target_year: 目標年（給 cohort 用，預設用 results 推算）
        sf_method: SF 計算方法（'farms' / 'cows' / 'mixed'）

    Returns:
        修改過的 results
    """
    if dhi_panel is None:
        from ..data._cow_count_extractor import extract_dhi_yearly_cows
        dhi_panel = extract_dhi_yearly_cows(years=list(range(2018, 2025)))

    # 只處理「全國」區段（區域已有自己的 region_static_sf 機制）
    nat = results.get("全國")
    if not nat:
        log.warning("  results 沒有全國區段、跳過 L4 校正")
        return results

    # 找出 series_history + ensemble.forecast 包含的所有年份
    years_needed = set()
    for p in nat.get("series_history", []):
        years_needed.add(int(str(p["yyyymm"])[:4]))
    if nat.get("ensemble"):
        for pt in nat["ensemble"]["forecast"]:
            years_needed.add(int(str(pt["yyyymm"])[:4]))

    sf_by_year = compute_l4_sf_by_year(sorted(years_needed), dhi_panel,
                                          method=sf_method)
    if not sf_by_year:
        log.warning("  無 L4 SF 可用、跳過")
        return results

    # 報告各年 SF
    log.info("  Level 4 SF：")
    for y in sorted(sf_by_year.keys())[-8:]:  # 只 log 最後 8 年
        info = sf_by_year[y]
        log.info(f"    {y}: SF={info['sf']:.3f}（官方場 "
                  f"{info['official_farms']:.0f} / DHI 場 "
                  f"{info['dhi_farms']:.0f}, {info['source']}）")

    def get_sf(yyyymm):
        y = int(str(yyyymm)[:4])
        return sf_by_year.get(y, {}).get("sf")

    # 產生 calibrated_l4
    hist_cal = []
    for p in nat.get("series_history", []):
        sf = get_sf(p["yyyymm"])
        if sf:
            hist_cal.append({"yyyymm": p["yyyymm"],
                              "value": float(p["value"]) * sf})
        else:
            hist_cal.append({"yyyymm": p["yyyymm"],
                              "value": float(p["value"])})

    models_cal = []
    for r in nat.get("models", []):
        if not r.get("success"):
            models_cal.append(r)
            continue
        new_fc = []
        for pt in r["forecast"]:
            sf = get_sf(pt["yyyymm"]) or 1.0
            new_fc.append({
                "yyyymm": pt["yyyymm"],
                "p50": pt["p50"] * sf,
                "p10": pt["p10"] * sf,
                "p90": pt["p90"] * sf,
            })
        models_cal.append({**r, "forecast": new_fc})

    ens_cal = None
    if nat.get("ensemble"):
        new_fc = []
        for pt in nat["ensemble"]["forecast"]:
            sf = get_sf(pt["yyyymm"]) or 1.0
            new_fc.append({
                "yyyymm": pt["yyyymm"],
                "p50": pt["p50"] * sf,
                "p10": pt["p10"] * sf,
                "p90": pt["p90"] * sf,
            })
        ens_cal = {**nat["ensemble"], "forecast": new_fc}

    # 算 future-only 加總
    forecast_total_p50 = sum(
        p["p50"] for p in (ens_cal["forecast"] if ens_cal else [])
    ) / 1000  # 公噸 → 千噸

    # === 階段二：加入 cohort 結構模型（直接 national、不過 SF）===
    if with_cohort:
        from .cohort_model import forecast_cohort_simple
        # 推算 target_year：用 ensemble forecast 第一個 yyyymm 的年份
        if target_year is None and ens_cal and ens_cal.get("forecast"):
            target_year = int(str(ens_cal["forecast"][0]["yyyymm"])[:4])
        if target_year:
            try:
                cohort_r = forecast_cohort_simple(target_year)
                if cohort_r.get("success"):
                    # cohort 直接 national 尺度、加進 models_cal
                    # 保留 baseline 參數給 What-If 情境計算用
                    models_cal.append({
                        "model": "cohort_simple",
                        "success": True,
                        "in_sample_mape": cohort_r["in_sample_mape"],
                        "forecast": cohort_r["forecast"],
                        "is_national": True,  # 標記不需 SF
                        "predicted_cows": cohort_r.get("predicted_cows"),
                        "predicted_daily_yield_kg": cohort_r.get("predicted_daily_yield_kg"),
                        "annual_total_tons": cohort_r.get("annual_total_tons"),
                        "target_year": target_year,
                    })
                    log.info(f"  Cohort v1 加入：年產量 "
                              f"{cohort_r['annual_total_tons']/10000:.1f} 萬公噸")
            except Exception as e:
                log.warning(f"  Cohort v1 失敗、跳過: {e}")

            # cohort v2 (n=quarterly + r=adaptive ensemble + as_of=today auto nowcast)
            try:
                from .cohort_model_v2 import forecast_cohort_v2
                from datetime import date
                v2_r = forecast_cohort_v2(
                    target_year=target_year,
                    n_projection='quarterly',
                    r_window='adaptive',
                    as_of_date=date.today().isoformat(),
                    nowcast_mode='auto')
                if v2_r.get("success"):
                    models_cal.append({
                        "model": "cohort_v2_auto",
                        "success": True,
                        "in_sample_mape": v2_r["in_sample_mape"],
                        "forecast": v2_r["forecast"],
                        "is_national": True,
                        "predicted_cows": v2_r.get("predicted_cows"),
                        "predicted_daily_yield_kg": v2_r.get("predicted_daily_yield_kg"),
                        "annual_total_tons": v2_r.get("annual_total_tons"),
                        "target_year": target_year,
                        "v2_config": v2_r.get("v2_config", {}),
                    })
                    log.info(f"  Cohort v2 加入：年產量 "
                              f"{v2_r['annual_total_tons']/10000:.1f} 萬公噸")
            except Exception as e:
                log.warning(f"  Cohort v2 失敗、跳過: {e}")

    # === 階段一：bias 校正（依 holdout backtest 每個模型的 bias）===
    bias_applied = {}
    if holdout and holdout.get("summary", {}).get("by_model_mape"):
        bm_mape = holdout["summary"]["by_model_mape"]
        # 對每個模型套 (1 - bias/100)
        for r in models_cal:
            m = r.get("model")
            if m in bm_mape and r.get("success"):
                bias_pct = bm_mape[m].get("bias", 0)
                factor = 1 - bias_pct / 100.0
                bias_applied[m] = bias_pct
                for pt in r["forecast"]:
                    pt["p50"] = pt["p50"] * factor
                    pt["p10"] = pt["p10"] * factor
                    pt["p90"] = pt["p90"] * factor
        # ensemble 也校正
        if ens_cal and "ensemble" in bm_mape:
            bias_pct = bm_mape["ensemble"].get("bias", 0)
            factor = 1 - bias_pct / 100.0
            bias_applied["ensemble"] = bias_pct
            for pt in ens_cal["forecast"]:
                pt["p50"] = pt["p50"] * factor
                pt["p10"] = pt["p10"] * factor
                pt["p90"] = pt["p90"] * factor
        if bias_applied:
            ba_str = ", ".join(f"{m}:{b:+.1f}%" for m, b in bias_applied.items())
            log.info(f"  Bias 校正套用：{ba_str}")

    # === 階段一：top-3 ensemble（用 holdout MAPE 加權、只取最佳 3 個）===
    ensemble_top3 = None
    if holdout and holdout.get("summary", {}).get("by_model_mape"):
        bm_mape = holdout["summary"]["by_model_mape"]
        # 排除 'ensemble' 自己；只看單一模型
        candidates = [(m, info["mape"]) for m, info in bm_mape.items()
                       if m != "ensemble" and info.get("mape") is not None]
        candidates.sort(key=lambda x: x[1])
        top_n = 3
        top_models = [m for m, _ in candidates[:top_n]]
        log.info(f"  Top-{top_n} 模型（依 holdout MAPE）：{top_models}")

        # 取每個模型的 forecast（已 bias-corrected）並加權平均
        # 權重 = 1 / holdout_mape
        weights = {m: 1.0/bm_mape[m]["mape"] for m in top_models
                    if bm_mape[m]["mape"] > 0}
        total_w = sum(weights.values())
        if total_w > 0:
            weights = {m: w/total_w for m, w in weights.items()}
            # 收集每個 top model 的 forecast
            top_forecasts = {}
            for r in models_cal:
                if r["model"] in top_models and r.get("success"):
                    top_forecasts[r["model"]] = r["forecast"]
            if len(top_forecasts) >= 2:
                # 取共同月份、加權
                first_m = next(iter(top_forecasts.values()))
                merged = []
                for i, pt in enumerate(first_m):
                    new_pt = {"yyyymm": pt["yyyymm"]}
                    for q in ("p50", "p10", "p90"):
                        v = sum(top_forecasts[m][i][q] * weights[m]
                                for m in top_forecasts)
                        new_pt[q] = v
                    merged.append(new_pt)
                ensemble_top3 = {
                    "forecast": merged,
                    "weights": weights,
                    "models": top_models,
                    "method": f"Top-{top_n} holdout-MAPE 加權",
                }

    # 計算最後 12 月 P50 加總（如果有 ensemble_top3 用它、否則 ensemble）
    final_ensemble = ensemble_top3 or ens_cal
    forecast_total_p50 = sum(
        p["p50"] for p in (final_ensemble["forecast"] if final_ensemble else [])
    ) / 1000

    nat["calibrated_l4"] = {
        "series_history": hist_cal,
        "models": models_cal,
        "ensemble": ens_cal,
        "ensemble_top3": ensemble_top3,
        "sf_by_year": sf_by_year,
        "method": "Level 4：季報+年報外推" + (
            "、含 bias 校正" if bias_applied else ""),
        "bias_applied": bias_applied,
        "forecast_total_tons_p50": forecast_total_p50 * 1000,
    }
    log.info(f"  Level 4 校正完成、未來 12 月 P50 加總 = "
              f"{forecast_total_p50*1000:,.0f} 公噸")

    return results
