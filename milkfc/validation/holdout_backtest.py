"""完整 pipeline holdout backtest。

對每個 holdout 年 Y：
1. 模型只看到 ≤ (Y-1) 的資料
2. 跑時序預測、未來 12 個月 DHI 加總
3. 乘 M2 scale factor（用 Y-1 的官方場數 / Y-1 的 DHI 場數）
4. 對照當年官方真值 → 算完整 pipeline 誤差

這個誤差才是「主管機關該看的真實實戰準度」。
"""
import json
import time
import logging
from pathlib import Path
from collections import defaultdict
import pandas as pd
import numpy as np

from .. import config
from ..data import load_combined
from ..data.official_inventory import OFFICIAL_DAIRY_INVENTORY
from ..data._cow_count_extractor import extract_dhi_yearly_cows
from ..data.quarterly_inventory import (QUARTERLY_INVENTORY,
                                          estimate_official_for_year,
                                          quarter_to_decimal_year)
from ..forecast.timeseries import (build_national_monthly_series,
                                     forecast_all, ensemble_forecast)

log = logging.getLogger("milkfc.backtest")


def run_holdout_backtest(holdout_years: list = None,
                          horizon_months: int = 12,
                          out_path: Path = None,
                          with_cohort: bool = False,
                          with_neural: bool = False) -> dict:
    """跑 holdout backtest。

    Args:
        holdout_years: 要回測的年份清單（預設 [2021, 2022, 2023, 2024]）
        horizon_months: 預測月數（預設 12）
        out_path: 結果存檔路徑（None 則只回傳）

    Returns:
        {
            "rows": [{year, dhi_actual_tons, dhi_predicted_tons, dhi_err_pct,
                       scale_factor_used, official_predicted_tons,
                       official_actual_tons, full_err_pct, models}, ...],
            "summary": {dhi_mape, full_mape, by_model_mape},
        }
    """
    if holdout_years is None:
        holdout_years = [2021, 2022, 2023, 2024]

    log.info("=" * 60)
    log.info(f"Holdout backtest: years={holdout_years}, horizon={horizon_months}")
    log.info("=" * 60)

    # 載入完整 DHI 資料 + DHI 場/牛快取
    t_start = time.time()
    df_all = load_combined(config.SNAPSHOT_DIR / "_cache.pkl")
    log.info(f"  DHI 全集載入：{len(df_all):,} rows")
    dhi_panel = extract_dhi_yearly_cows(years=list(range(2017, 2025)))

    rows = []
    by_model_errors = defaultdict(list)

    for y in holdout_years:
        log.info(f"\n{'─' * 50}")
        log.info(f"Year {y} holdout：")

        # 1) 過濾 DHI ≤ (y-1) 年底
        cutoff = pd.Timestamp(f"{y-1}-12-31")
        df_train = df_all[df_all["sample_date"] <= cutoff].copy()
        log.info(f"  訓練資料截至 {cutoff.date()}: {len(df_train):,} rows")

        # 篩活躍場（最近 180 天）
        if len(df_train) == 0:
            log.warning(f"  Year {y}: 無訓練資料、跳過")
            continue
        max_date = df_train["sample_date"].max()
        active_cutoff = max_date - pd.Timedelta(days=180)
        farm_latest = df_train.groupby("farm_id")["sample_date"].max()
        active = farm_latest[farm_latest >= active_cutoff].index.tolist()
        df_train = df_train[df_train["farm_id"].isin(active)]
        log.info(f"  活躍場：{len(active)}")

        # 2) 建全國月度序列（高門檻）
        series = build_national_monthly_series(
            df_train, min_farms_per_month=100, min_records_per_month=500)
        log.info(f"  時序長度: {len(series)} 個月（{series.index[0]} ~ {series.index[-1]}）")

        if len(series) < 36:
            log.warning(f"  Year {y}: 序列太短、跳過")
            continue

        # 3) 跑五個時序模型 + ensemble
        ts_results = forecast_all(series, horizon=horizon_months,
                                     with_neural=with_neural)
        ensemble = ensemble_forecast(ts_results)
        for r in ts_results:
            if r.get("success"):
                log.info(f"  {r['model']:<18} in-sample MAPE = "
                          f"{r.get('in_sample_mape',0):.1f}%")

        # 4) 取出年 Y 預測、加總 12 個月 DHI 公噸
        if not ensemble:
            log.warning(f"  Year {y}: ensemble 失敗、跳過")
            continue
        forecast_months = [pt["yyyymm"] for pt in ensemble["forecast"]]
        # 只取屬於 year y 的月份
        target_months = [m for m in forecast_months if m.startswith(str(y))]
        ensemble_y = sum(pt["p50"] for pt in ensemble["forecast"]
                          if pt["yyyymm"].startswith(str(y))) / 1000
        # 各模型也取 year y
        model_predictions_y = {}
        for r in ts_results:
            if r.get("success"):
                m_total = sum(pt["p50"] for pt in r["forecast"]
                                if pt["yyyymm"].startswith(str(y))) / 1000
                model_predictions_y[r["model"]] = m_total
        log.info(f"  Year {y} ensemble 預測 DHI 年加總 = {ensemble_y:,.0f} 公噸")

        # 5) 拿 year y 真實 DHI 加總（從原 series_history 算）
        df_actual_y = df_all[df_all["sample_date"].dt.year == y]
        # 用同樣的 build_national_monthly_series 邏輯估「真實 DHI 加總」
        series_y = build_national_monthly_series(
            df_actual_y, min_farms_per_month=100, min_records_per_month=500)
        dhi_actual_y = float(series_y.sum()) / 1000  # kg → 公噸
        dhi_err_pct = (ensemble_y - dhi_actual_y) / dhi_actual_y * 100
        log.info(f"  Year {y} 實際 DHI 年加總 = {dhi_actual_y:,.0f} 公噸 "
                  f"(預測誤差 {dhi_err_pct:+.1f}%)")

        # 6) 計算多種 SF（測試 Level 1/2/4 的差異）
        prev_panel = dhi_panel.get(y - 1)
        prev_off = OFFICIAL_DAIRY_INVENTORY.get(y - 1)

        # Level 1：直接用 Y-1 場數比（原本的方法）
        sf_l1 = (prev_off["n_farms"] / prev_panel["n_farms"]
                  if prev_panel and prev_off else None)

        # Level 2：分子分母都做線性外推
        # 官方場數：用 ≤ Y-1 年的官方資料外推到 Y
        annual_pre = {yy: v for yy, v in OFFICIAL_DAIRY_INVENTORY.items()
                       if yy < y}
        quarterly_pre = {qid: v for qid, v in QUARTERLY_INVENTORY.items()
                          if quarter_to_decimal_year(qid) < y}
        try:
            est_off = estimate_official_for_year(
                y, quarterly=quarterly_pre, annual=annual_pre)
        except Exception:
            est_off = None

        # DHI 歷史（場數 + 牛口）只取 ≤ Y-1
        dhi_history = [(yy, v["n_farms"], v.get("n_cows"))
                        for yy, v in dhi_panel.items() if yy < y]
        dhi_history.sort()
        dhi_history_recent = dhi_history[-4:] if len(dhi_history) >= 4 else dhi_history

        # 三種 SF 方法
        sf_l4_farms = None     # 場數比（原 sf_l4）
        sf_l4_cows = None      # 牛口比（新）
        sf_l4_mixed = None     # 50/50 混合（新）
        est_dhi_farms_y = None
        est_dhi_cows_y = None
        sf_l4_source = None

        if est_off and len(dhi_history_recent) >= 2:
            xs = np.array([p[0] + 0.5 for p in dhi_history_recent])
            # 場數外推
            ys_f = np.array([p[1] for p in dhi_history_recent])
            slope_f, b_f = np.polyfit(xs, ys_f, 1)
            est_dhi_farms_y = float(slope_f * (y + 0.5) + b_f)
            est_dhi_farms_y = max(est_dhi_farms_y,
                                    dhi_history_recent[-1][1] * 0.95)
            sf_l4_farms = est_off["n_dairy_farms"] / est_dhi_farms_y

            # 牛口外推（若資料齊）
            cow_pts = [(p[0], p[2]) for p in dhi_history_recent
                        if p[2] is not None and p[2] > 0]
            if (len(cow_pts) >= 2
                    and est_off.get("n_milking_cows", 0) > 0):
                xs_c = np.array([p[0] + 0.5 for p in cow_pts])
                ys_c = np.array([p[1] for p in cow_pts])
                slope_c, b_c = np.polyfit(xs_c, ys_c, 1)
                est_dhi_cows_y = float(slope_c * (y + 0.5) + b_c)
                est_dhi_cows_y = max(est_dhi_cows_y, cow_pts[-1][1] * 0.95)
                sf_l4_cows = est_off["n_milking_cows"] / est_dhi_cows_y
                sf_l4_mixed = 0.5 * sf_l4_farms + 0.5 * sf_l4_cows

            sf_l4_source = est_off.get("source", "extrapolation")

        # 為了向下相容、保留 sf_l4 = sf_l4_farms（場數比、原方法）
        sf_l4 = sf_l4_farms

        # 預設用 L4 farms（即原行為）
        sf_used = sf_l4 if sf_l4 else sf_l1

        # 7) 預測全國產量（每種 SF 方法都算一個）
        full_pred = ensemble_y * sf_used if sf_used else None
        full_pred_l4_farms = (ensemble_y * sf_l4_farms
                                if sf_l4_farms else None)
        full_pred_l4_cows = (ensemble_y * sf_l4_cows
                                if sf_l4_cows else None)
        full_pred_l4_mixed = (ensemble_y * sf_l4_mixed
                                 if sf_l4_mixed else None)

        # 8) 對照官方真值
        official_y = OFFICIAL_DAIRY_INVENTORY.get(y, {}).get("production_tons")
        def _err(pred):
            return ((pred - official_y) / official_y * 100
                    if pred and official_y else None)
        full_err = _err(full_pred)
        full_err_l4_farms = _err(full_pred_l4_farms)
        full_err_l4_cows = _err(full_pred_l4_cows)
        full_err_l4_mixed = _err(full_pred_l4_mixed)

        # 同時記錄 L1 預測供比較
        full_pred_l1 = ensemble_y * sf_l1 if sf_l1 else None
        full_err_l1 = _err(full_pred_l1)

        log.info(f"  SF L1 (Y-1 raw)        = "
                  f"{sf_l1:.3f}" if sf_l1 else "  SF L1 缺")
        if sf_l4_farms:
            log.info(f"  SF L4 場數比          = {sf_l4_farms:.3f}（{sf_l4_source}）")
        if sf_l4_cows:
            log.info(f"  SF L4 牛口比          = {sf_l4_cows:.3f}")
        if sf_l4_mixed:
            log.info(f"  SF L4 混合 50/50      = {sf_l4_mixed:.3f}")
        if full_pred and official_y:
            log.info(f"  全國預測（L4 場數比）= {full_pred_l4_farms:,.0f} 公噸"
                      f"（誤差 {full_err_l4_farms:+.1f}%）")
            if full_pred_l4_cows:
                log.info(f"  全國預測（L4 牛口比）= {full_pred_l4_cows:,.0f} 公噸"
                          f"（誤差 {full_err_l4_cows:+.1f}%）")
            if full_pred_l4_mixed:
                log.info(f"  全國預測（L4 混合）  = {full_pred_l4_mixed:,.0f} 公噸"
                          f"（誤差 {full_err_l4_mixed:+.1f}%）")
            log.info(f"  全國預測（L1 baseline）= "
                      f"{full_pred_l1:,.0f} 公噸（誤差 {full_err_l1:+.1f}%）")
            log.info(f"  官方真值              = {official_y:,.0f} 公噸")

        rows.append({
            "year": y,
            "training_cutoff": str(cutoff.date()),
            "training_n_rows": int(len(df_train)),
            "training_n_farms": len(active),
            "series_length": len(series),
            "dhi_actual_tons": dhi_actual_y,
            "dhi_predicted_tons": ensemble_y,
            "dhi_err_pct": dhi_err_pct,
            "model_predictions": model_predictions_y,
            # 多版本 SF（給對比看）
            "sf_l1": sf_l1,                # 原方法（Y-1 raw）
            "sf_l4": sf_l4,                # Level 4 場數比（向下相容）
            "sf_l4_farms": sf_l4_farms,    # Level 4 場數比（明確命名）
            "sf_l4_cows": sf_l4_cows,      # Level 4 牛口比（新）
            "sf_l4_mixed": sf_l4_mixed,    # Level 4 50/50 混合（新）
            "sf_l4_source": sf_l4_source,
            "scale_factor_m2": sf_l1,      # 保留向下相容
            "est_dhi_farms": est_dhi_farms_y,
            "est_dhi_cows": est_dhi_cows_y,
            "est_official_farms": (est_off.get("n_dairy_farms")
                                      if est_off else None),
            "est_official_cows": (est_off.get("n_milking_cows")
                                     if est_off else None),
            # 預設用 Level 4 場數比
            "full_predicted_tons": full_pred,
            "full_predicted_tons_l1": full_pred_l1,
            "full_predicted_tons_l4_farms": full_pred_l4_farms,
            "full_predicted_tons_l4_cows": full_pred_l4_cows,
            "full_predicted_tons_l4_mixed": full_pred_l4_mixed,
            "full_actual_tons": official_y,
            "full_err_pct": full_err,
            "full_err_pct_l1": full_err_l1,
            "full_err_pct_l4_farms": full_err_l4_farms,
            "full_err_pct_l4_cows": full_err_l4_cows,
            "full_err_pct_l4_mixed": full_err_l4_mixed,
        })

        if full_err is not None:
            by_model_errors["ensemble"].append(full_err)
        for m, pred in model_predictions_y.items():
            if sf_used and official_y:
                err = (pred * sf_used - official_y) / official_y * 100
                by_model_errors[m].append(err)

        # === 階段二：Cohort 結構模型 backtest（v1 simple）===
        if with_cohort and official_y:
            try:
                from ..forecast.cohort_model import forecast_cohort_simple
                cohort_r = forecast_cohort_simple(
                    target_year=y, horizon_months=12,
                    history_max_year=y - 1)
                if cohort_r.get("success"):
                    cohort_pred_tons = cohort_r["annual_total_tons"]
                    cohort_err = (cohort_pred_tons - official_y) / official_y * 100
                    by_model_errors["cohort_simple"].append(cohort_err)
                    log.info(f"  Cohort v1 預測 = {cohort_pred_tons:,.0f} 公噸 "
                              f"（誤差 {cohort_err:+.1f}%）")
                    rows[-1]["cohort_predicted_tons"] = cohort_pred_tons
                    rows[-1]["cohort_err_pct"] = cohort_err
            except Exception as e:
                log.warning(f"  Cohort v1 backtest 失敗: {e}")

        # === 階段三：Cohort v2 auto（n=quarterly + r=adaptive ensemble）===
        # 為公平比較，回測一律純外推（as_of=cutoff，無 target year 資料）
        if with_cohort and official_y:
            try:
                from ..forecast.cohort_model_v2 import forecast_cohort_v2
                v2_r = forecast_cohort_v2(
                    target_year=y, horizon_months=12,
                    history_max_year=y - 1,
                    n_projection='quarterly',
                    r_window='adaptive',
                    as_of_date=str(cutoff.date()),  # 純外推
                    nowcast_mode='auto')  # auto 在 cutoff 看不到 target year 資料、退化純外推
                if v2_r.get("success"):
                    v2_pred = v2_r["annual_total_tons"]
                    v2_err = (v2_pred - official_y) / official_y * 100
                    by_model_errors["cohort_v2_auto"].append(v2_err)
                    log.info(f"  Cohort v2 (auto) 預測 = {v2_pred:,.0f} 公噸 "
                              f"（誤差 {v2_err:+.1f}%）")
                    rows[-1]["cohort_v2_predicted_tons"] = v2_pred
                    rows[-1]["cohort_v2_err_pct"] = v2_err
            except Exception as e:
                log.warning(f"  Cohort v2 backtest 失敗: {e}")

    # Summary
    dhi_errs = [abs(r["dhi_err_pct"]) for r in rows
                  if r["dhi_err_pct"] is not None]
    full_errs = [abs(r["full_err_pct"]) for r in rows
                   if r["full_err_pct"] is not None]
    full_errs_l1 = [abs(r["full_err_pct_l1"]) for r in rows
                      if r.get("full_err_pct_l1") is not None]
    # 三種 SF 方法的 MAPE 與 bias（ensemble 路徑）
    def _stat(field):
        errs = [r[field] for r in rows if r.get(field) is not None]
        if not errs:
            return None
        return {
            "mape": float(np.mean([abs(e) for e in errs])),
            "bias": float(np.mean(errs)),
            "n": len(errs),
        }
    sf_compare = {
        "L4_farms": _stat("full_err_pct_l4_farms"),
        "L4_cows": _stat("full_err_pct_l4_cows"),
        "L4_mixed": _stat("full_err_pct_l4_mixed"),
        "L1_farms": _stat("full_err_pct_l1"),
    }
    by_model_mape = {
        m: {
            "mape": float(np.mean([abs(e) for e in errs])),
            "bias": float(np.mean(errs)),
            "n": len(errs),
        } for m, errs in by_model_errors.items() if errs
    }

    summary = {
        "n_years": len(rows),
        "dhi_mape": float(np.mean(dhi_errs)) if dhi_errs else None,
        "full_mape": float(np.mean(full_errs)) if full_errs else None,
        "full_mape_l1": (float(np.mean(full_errs_l1))
                         if full_errs_l1 else None),
        "by_model_mape": by_model_mape,
        "sf_compare": sf_compare,  # 三種 SF 方法的 MAPE/bias 比較
        "best_model": (min(by_model_mape.items(),
                            key=lambda x: x[1]["mape"])[0]
                        if by_model_mape else None),
        "sf_method": "Level 4（場數線性外推 + 季報資料）",
    }

    out = {
        "rows": rows,
        "summary": summary,
        "elapsed_seconds": time.time() - t_start,
    }

    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, ensure_ascii=False, default=str)
        log.info(f"\n結果已存：{out_path}")
    else:
        # 預設存到 snapshot
        default = config.SNAPSHOT_DIR / "_holdout_backtest.json"
        default.parent.mkdir(parents=True, exist_ok=True)
        with open(default, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, ensure_ascii=False, default=str)
        log.info(f"\n結果已存：{default}")

    log.info("\n" + "=" * 60)
    log.info("Backtest 摘要：")
    log.info(f"  完整 pipeline MAPE = {summary['full_mape']:.1f}% "
              f"({len(full_errs)} 年)")
    log.info(f"  時序 (DHI) MAPE   = {summary['dhi_mape']:.1f}%")

    # === SF 方法比較（4 種、ensemble 路徑）===
    log.info("")
    log.info(f"  {'SF 方法 對比':<22}{'MAPE':>8}{'Bias':>10}{'樣本':>6}")
    method_labels = {
        "L4_farms": "L4 場數比（原方法）",
        "L4_cows":  "L4 牛口比（新）",
        "L4_mixed": "L4 場數+牛口 50/50",
        "L1_farms": "L1 baseline (Y-1)",
    }
    sf_cmp = summary.get("sf_compare", {})
    for k in ("L4_farms", "L4_cows", "L4_mixed", "L1_farms"):
        s = sf_cmp.get(k)
        if not s:
            continue
        log.info(f"  {method_labels[k]:<22}"
                  f"{s['mape']:>6.1f}%"
                  f"{s['bias']:>+8.1f}%"
                  f"{s['n']:>6}")

    log.info("")
    log.info(f"  最佳單一模型: {summary['best_model']}")
    for m, s in sorted(by_model_mape.items(), key=lambda x: x[1]["mape"]):
        log.info(f"    {m:<18} MAPE={s['mape']:>5.1f}% "
                  f"bias={s['bias']:>+5.1f}% (n={s['n']})")
    log.info("=" * 60)

    return out
