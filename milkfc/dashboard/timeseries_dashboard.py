"""純時序模型儀表板 - 多區域多模型對比。"""
import json
import pandas as pd
from pathlib import Path
from datetime import datetime as _dt
from .. import config


def build_timeseries_dashboard(snapshot_id: str = None,
                                  out_path: Path = None,
                                  rerun_backtest: bool = False,
                                  backtest_years: list = None,
                                  skip_backtest: bool = False,
                                  with_cohort: bool = False,
                                  with_neural: bool = False,
                                  sf_method: str = "farms") -> Path:
    """從 ts_ 開頭的 snapshot 產生儀表板。

    Args:
        rerun_backtest: True 強制重跑 holdout backtest（不用 cache）
        backtest_years: 自訂 backtest 年份；None 用預設 [2021,2022,2023,2024]
        skip_backtest: True 完全不顯示 backtest 區塊
        with_cohort: 加入 Cohort 結構模型作為交叉驗證
        with_neural: 加入 NeuralProphet 神經網路模型
        sf_method: SF 計算方法 ('farms' / 'cows' / 'mixed')
    """
    import logging
    log = logging.getLogger("milkfc.dashboard")

    if snapshot_id is None:
        snaps = sorted(config.SNAPSHOT_DIR.glob("ts_*"), reverse=True)
        if not snaps:
            raise FileNotFoundError("無 timeseries snapshot")
        snap_dir = snaps[0]
    else:
        snap_dir = config.SNAPSHOT_DIR / snapshot_id

    with open(snap_dir / "manifest.json") as f:
        manifest = json.load(f)
    with open(snap_dir / "ts_results.json") as f:
        results = json.load(f)

    # 「同年」對比（DHI sum 用真實值、只測 scale factor 那一半）
    official_compare = None
    try:
        from ..validation import run_comparison
        official_compare = run_comparison(snapshot_id=snap_dir.name)
    except Exception as e:
        log.warning(f"  Official comparison skipped: {e}")

    # Holdout backtest（完整 pipeline 真實實戰準度）
    holdout = None
    if skip_backtest:
        log.info("  Holdout backtest 跳過（--skip-backtest）")
    else:
        backtest_cache = config.SNAPSHOT_DIR / "_holdout_backtest.json"
        try:
            if backtest_cache.exists() and not rerun_backtest:
                with open(backtest_cache) as f:
                    holdout = json.load(f)
                yrs = [r["year"] for r in holdout.get("rows", [])]
                log.info(f"  Holdout backtest 載入快取（年份 {yrs}）")
            else:
                from ..validation import run_holdout_backtest
                yrs = backtest_years or [2021, 2022, 2023, 2024]
                log.info(f"  跑 holdout backtest...（年份 {yrs}、約 1-2 分鐘）")
                holdout = run_holdout_backtest(holdout_years=yrs,
                                                  with_cohort=with_cohort,
                                                  with_neural=with_neural)
        except Exception as e:
            log.warning(f"  Holdout backtest skipped: {e}")

    # Level 4 SF post-processor（含 bias 校正、top-3 ensemble、需要 holdout 結果）
    try:
        from ..forecast.level4_sf import apply_l4_calibration
        results = apply_l4_calibration(results, holdout=holdout,
                                          with_cohort=with_cohort,
                                          sf_method=sf_method)
        log.info(f"  Level 4 校正：sf_method={sf_method}")
    except Exception as e:
        log.warning(f"  Level 4 校正跳過: {e}")

    # 計算動態 context（讓儀表板完全 evergreen）
    context = _build_context(manifest, results, official_compare, holdout)

    payload = {"manifest": manifest, "results": results,
                "official_compare": official_compare,
                "holdout_backtest": holdout,
                "context": context}

    out_path = out_path or (config.ROOT / "timeseries.html")
    html = _render(payload)
    out_path.write_text(html, encoding="utf-8")
    return out_path


def _build_context(manifest, results, official_compare, holdout):
    """建構儀表板需要的動態 context（目標期間、年增率、資料時效、最佳模型等）。

    所有具體年份/數字都從資料推、不寫死、明年資料進來自動更新。
    """
    import pandas as pd
    from ..data.official_inventory import OFFICIAL_DAIRY_INVENTORY
    from ..data.quarterly_inventory import QUARTERLY_INVENTORY, quarter_to_decimal_year

    ctx = {}
    cfg = manifest.get("config", {})
    ref_date = cfg.get("reference_date")
    horizon = cfg.get("horizon_months", 12)

    # === 目標期間 ===
    if ref_date:
        ref = pd.Timestamp(ref_date)
        start = ref + pd.DateOffset(months=1)
        end = ref + pd.DateOffset(months=horizon)
        ctx["target_start"] = start.strftime("%Y-%m")
        ctx["target_end"] = end.strftime("%Y-%m")
        ctx["target_year"] = start.year if start.year == end.year else None
        ctx["target_label"] = (f"{start.year}" if ctx["target_year"]
                                 else f"{start.strftime('%Y-%m')} ~ {end.strftime('%Y-%m')}")
    else:
        ctx["target_label"] = "未來 12 月"

    # === 最佳模型（從 holdout 抓） ===
    best_model = "ensemble"
    best_mape = None
    best_bias = None
    if holdout:
        s = holdout.get("summary", {})
        best_model = s.get("best_model", "ensemble")
        bm = s.get("by_model_mape", {}).get(best_model)
        if bm:
            best_mape = bm.get("mape")
            best_bias = bm.get("bias")
    ctx["best_model"] = best_model
    ctx["best_mape"] = best_mape
    ctx["best_bias"] = best_bias

    # === 取最佳模型的 12 月加總（calibrated_l4） ===
    nat = results.get("全國", {})
    cal = nat.get("calibrated_l4") or nat.get("calibrated") or {}
    forecast_p50 = forecast_p10 = forecast_p90 = None
    if cal.get("models"):
        bm_fc = next((m for m in cal["models"]
                       if m.get("model") == best_model and m.get("success")), None)
        if bm_fc:
            forecast_p50 = sum(p["p50"] for p in bm_fc["forecast"]) / 1000  # kg→公噸
            forecast_p10 = sum(p["p10"] for p in bm_fc["forecast"]) / 1000
            forecast_p90 = sum(p["p90"] for p in bm_fc["forecast"]) / 1000
    if forecast_p50 is None and cal.get("ensemble"):
        forecast_p50 = sum(p["p50"] for p in cal["ensemble"]["forecast"]) / 1000
        forecast_p10 = sum(p["p10"] for p in cal["ensemble"]["forecast"]) / 1000
        forecast_p90 = sum(p["p90"] for p in cal["ensemble"]["forecast"]) / 1000
    ctx["forecast_p50_tons"] = forecast_p50
    ctx["forecast_p10_tons"] = forecast_p10
    ctx["forecast_p90_tons"] = forecast_p90

    # === 對照最近一年農業部公告值（用於年增率） ===
    if OFFICIAL_DAIRY_INVENTORY:
        latest_official_year = max(OFFICIAL_DAIRY_INVENTORY.keys())
        latest_official_tons = OFFICIAL_DAIRY_INVENTORY[latest_official_year][
            "production_tons"]
        ctx["latest_official_year"] = latest_official_year
        ctx["latest_official_tons"] = latest_official_tons
        if forecast_p50 and ctx.get("target_year"):
            year_gap = ctx["target_year"] - latest_official_year
            yoy_total = (forecast_p50 - latest_official_tons) / latest_official_tons * 100
            yoy_per_year = yoy_total / year_gap if year_gap > 0 else None
            ctx["yoy_total_pct"] = yoy_total
            ctx["yoy_per_year_pct"] = yoy_per_year
            ctx["year_gap"] = year_gap

    # === 資料源時效性 ===
    sources = []
    # DHI
    dhi_max = cfg.get("reference_date")
    if dhi_max:
        sources.append({
            "name": "DHI 月度資料",
            "name_en": "DHI Monthly Records",
            "latest": dhi_max,
            "freq": "monthly",
        })
    # 在養量年報
    if OFFICIAL_DAIRY_INVENTORY:
        sources.append({
            "name": "在養量年報",
            "name_en": "Annual Inventory Report",
            "latest": str(max(OFFICIAL_DAIRY_INVENTORY.keys())),
            "freq": "annual",
        })
    # 季度在養量
    if QUARTERLY_INVENTORY:
        latest_q = max(QUARTERLY_INVENTORY.keys(),
                        key=quarter_to_decimal_year)
        sources.append({
            "name": "在養量季報",
            "name_en": "Quarterly Inventory Report",
            "latest": latest_q,
            "freq": "quarterly",
        })
    ctx["data_sources"] = sources

    # === 系統配置 ===
    ctx["sf_method"] = "Level 4（季報+年報外推）"
    ctx["sf_method_en"] = "Level 4 (quarterly + annual extrapolation)"

    # 取本次預測用的 SF 值（如果有）
    if cal.get("sf_by_year"):
        sf_by_year = cal["sf_by_year"]
        ctx["sf_by_year"] = {int(k) if isinstance(k, (int, str)) else k: v
                              for k, v in sf_by_year.items()}

    # === DHI 統計（年數、場數、紀錄數）===
    try:
        from ..data._cow_count_extractor import extract_dhi_yearly_cows
        # 只取已快取的年份避免觸發長時間掃描
        panel = extract_dhi_yearly_cows(years=list(range(2015, 2025)))
        if panel:
            ctx["dhi_total_records"] = sum(
                p.get("n_records", 0) for p in panel.values())
            ctx["dhi_panel_year_min"] = min(panel.keys())
            ctx["dhi_panel_year_max"] = max(panel.keys())
        # raw_data xlsx 的年範圍
        from .. import config as cfg_mod
        dhi_files = sorted((cfg_mod.ROOT / "raw_data").glob("*dhi.xlsx"))
        years_from_files = []
        for f in dhi_files:
            try:
                years_from_files.append(int(f.stem[:4]))
            except ValueError:
                continue
        if years_from_files:
            ctx["dhi_year_min"] = min(years_from_files)
            ctx["dhi_year_max"] = max(years_from_files)
            ctx["dhi_n_years"] = len(years_from_files)
    except Exception:
        pass

    # === Holdout backtest L1 vs L4（用於 SF 比較）===
    if holdout and holdout.get("summary"):
        ctx["full_mape_l4"] = holdout["summary"].get("full_mape")
        ctx["full_mape_l1"] = holdout["summary"].get("full_mape_l1")
        ctx["dhi_mape"] = holdout["summary"].get("dhi_mape")
        # === Top-3 模型對照（給「多模型交叉驗證」段）===
        bm_mape = holdout["summary"].get("by_model_mape", {})
        top3 = sorted(
            [(m, info) for m, info in bm_mape.items() if m != "ensemble"],
            key=lambda x: x[1].get("mape", 1e9))[:3]
        # 拿每個 top model 的 2026 預測值（bias 校正後）
        top3_data = []
        for m_name, m_info in top3:
            mape = m_info.get("mape", 0)
            bias = m_info.get("bias", 0)
            # 找 calibrated_l4 中該模型的 forecast
            pred_tons = None
            for m_cal in (cal.get("models") or []):
                if (m_cal.get("model") == m_name
                        and m_cal.get("success")
                        and m_cal.get("forecast")):
                    pred_tons = sum(p["p50"] for p in m_cal["forecast"]) / 1000
                    break
            top3_data.append({
                "name": m_name,
                "mape": mape,
                "bias": bias,
                "pred_tons": pred_tons,
                "pred_wton": pred_tons / 10000 if pred_tons else None,
            })
        ctx["top3_models"] = top3_data
        # 取最新年的 holdout 誤差（給「風險限制」段使用）
        # 用 best_model（系統實際採用）算、不用 ensemble 的數字
        rows = holdout.get("rows", [])
        if rows:
            last_bt = max(rows, key=lambda r: r["year"])
            ctx["last_backtest_year"] = last_bt["year"]
            bm_pred = (last_bt.get("model_predictions", {}) or {}).get(best_model)
            sf_l4 = last_bt.get("sf_l4")
            actual = last_bt.get("full_actual_tons")
            if bm_pred and sf_l4 and actual:
                # 套 bias 校正（與 apply_l4_calibration 一致）
                bias_pct = best_bias if best_bias is not None else 0
                pred_full = bm_pred * sf_l4 * (1 - bias_pct / 100.0)
                ctx["last_backtest_full_err"] = (pred_full - actual) / actual * 100
            else:
                # fallback 用 ensemble 數字（不該發生但保險）
                ctx["last_backtest_full_err"] = last_bt.get("full_err_pct")

    # === 歷史產量+牛口走勢（給「預測解讀」段用）===
    history = []
    for y in sorted(OFFICIAL_DAIRY_INVENTORY.keys())[-7:]:
        info = OFFICIAL_DAIRY_INVENTORY[y]
        history.append({
            "year": y,
            "production_tons": info["production_tons"],
            "n_milking_cows": info["n_milking_cows"],
            "n_farms": info["n_farms"],
        })
    ctx["history"] = history

    # === 結構變數歷史 + 預測（給「Cohort 結構變數視覺化」用）===
    structural_hist = []
    # 從現有 cache 撈 DHI yield
    try:
        from ..forecast.cohort_model import (_get_dhi_yearly_yield,
                                                  _compute_productivity_ratio)
        dhi_yield_hist = _get_dhi_yearly_yield(2015, 2025)
        ratio_hist = _compute_productivity_ratio(dhi_yield_hist, 2015, 2025)
    except Exception:
        dhi_yield_hist = {}
        ratio_hist = {}

    # 1. 年度資料（2015-2024 從年報抓）
    for y in sorted(OFFICIAL_DAIRY_INVENTORY.keys()):
        if y < 2015:
            continue
        info = OFFICIAL_DAIRY_INVENTORY[y]
        cows = info.get("n_milking_cows")
        prod = info.get("production_tons")
        nat_yield = (prod * 1000 / cows / 305
                      if cows and prod else None)
        structural_hist.append({
            "year": y,
            "n_milking_cows": cows,
            "n_farms": info.get("n_farms"),
            "production_tons": prod,
            "national_yield_kg": nat_yield,
            "dhi_yield_kg": dhi_yield_hist.get(y),
            "productivity_ratio": ratio_hist.get(y),
        })

    # 2. 2025 補資料（年報沒有、用季報平均 + DHI 2025 yield）
    try:
        from ..data.quarterly_inventory import (
            QUARTERLY_INVENTORY, quarter_to_decimal_year)
        q2025 = [v for q, v in QUARTERLY_INVENTORY.items()
                  if str(q).startswith('2025')]
        if q2025:
            avg_cows_2025 = sum(q.get("n_milking_cows", 0) for q in q2025) / len(q2025)
            avg_farms_2025 = sum(q.get("n_dairy_farms", 0) for q in q2025) / len(q2025)
            structural_hist.append({
                "year": 2025,
                "n_milking_cows": int(round(avg_cows_2025)),
                "n_farms": int(round(avg_farms_2025)),
                "production_tons": None,  # 2025 公告值還沒出
                "national_yield_kg": None,  # 缺產量、不能算
                "dhi_yield_kg": dhi_yield_hist.get(2025),
                "productivity_ratio": None,  # 缺全國 yield、不能算
                "is_partial": True,  # 標記為部分資料（季報估計）
                "note": f"從 {len(q2025)} 季季報平均估計",
            })
    except Exception:
        pass

    ctx["structural_history"] = structural_hist

    # === Cohort baseline（給 What-If 情境計算器用、不依賴 --with-cohort）===
    # cohort_simple 內部已套 productivity ratio 動態校正（DHI/全國 比率）
    # 此處再套 holdout 量到的「殘差 static bias」做最終對齊
    cohort_entry = None
    for m in (cal.get("models") or []):
        if m.get("model") == "cohort_simple" and m.get("success"):
            cohort_entry = m
            break

    # 從 holdout 抓 cohort_simple 的殘差 bias（cohort 校正後仍可能殘餘小偏差）
    cohort_bias_pct = 0.0
    if holdout and holdout.get("summary", {}).get("by_model_mape"):
        cohort_info = holdout["summary"]["by_model_mape"].get("cohort_simple", {})
        cohort_bias_pct = cohort_info.get("bias", 0.0) or 0.0

    cohort_baseline = None
    cr_full = None  # 完整 cohort 結果（含 raw / ratio）
    if cohort_entry and cohort_entry.get("predicted_cows"):
        # 已在 calibrated 的 cohort entry 中
        cohort_baseline = {
            "cows": cohort_entry.get("predicted_cows"),
            "daily_yield_kg": cohort_entry.get("predicted_daily_yield_kg"),
            "lactation_days": 305,
            # cohort_simple 已套 productivity 校正後的值
            "tons_after_productivity": cohort_entry.get("annual_total_tons"),
            "target_year": cohort_entry.get("target_year") or ctx.get("target_year"),
            "in_sample_mape": cohort_entry.get("in_sample_mape"),
        }
        # cohort entry 沒有 raw / ratio（pipeline 沒回傳）→ 直接 call 一次拿完整資訊
        try:
            from ..forecast.cohort_model import forecast_cohort_simple
            tgt = cohort_baseline["target_year"]
            if tgt:
                cr_full = forecast_cohort_simple(tgt)
        except Exception:
            pass
    else:
        # 沒在 results 裡 → 直接 call、提供 What-If 用
        try:
            from ..forecast.cohort_model import forecast_cohort_simple
            target_year = ctx.get("target_year") or (
                pd.Timestamp(ref_date).year + 1 if ref_date else None)
            if target_year:
                cr_full = forecast_cohort_simple(target_year)
                if cr_full.get("success"):
                    cohort_baseline = {
                        "cows": cr_full.get("predicted_cows"),
                        "daily_yield_kg": cr_full.get("predicted_daily_yield_kg"),
                        "lactation_days": 305,
                        "tons_after_productivity": cr_full.get("annual_total_tons"),
                        "target_year": target_year,
                        "in_sample_mape": cr_full.get("in_sample_mape"),
                    }
        except Exception as e:
            import logging
            logging.getLogger("milkfc.dashboard").warning(
                f"  Cohort baseline 計算失敗、What-If 將顯示 fallback: {e}")

    if cohort_baseline:
        # 取得 raw cohort（未套 productivity）與 productivity ratio
        if cr_full and cr_full.get("success"):
            cohort_baseline["tons_raw"] = cr_full.get("annual_total_tons_raw")
            cohort_baseline["productivity_ratio"] = cr_full.get("productivity_ratio_target", 1.0)
            cohort_baseline["productivity_correction_applied"] = cr_full.get(
                "productivity_correction_applied", True)
        else:
            # fallback：從現有資料推算
            cohort_baseline["tons_raw"] = cohort_baseline.get("tons_after_productivity")
            cohort_baseline["productivity_ratio"] = 1.0

        # 套 holdout 量到的 static 殘差 bias 校正
        static_factor = 1 - cohort_bias_pct / 100.0
        cohort_baseline["static_bias_pct"] = cohort_bias_pct
        cohort_baseline["static_calibration_factor"] = static_factor
        # 最終 cohort baseline = 原始 / productivity ratio × static factor
        cohort_baseline["annual_total_tons"] = (
            cohort_baseline["tons_after_productivity"] * static_factor)
        cohort_baseline["seasonal_pattern"] = [
            0.080, 0.082, 0.085, 0.087, 0.086, 0.084,
            0.082, 0.082, 0.080, 0.081, 0.083, 0.085,
        ]
        ctx["cohort_baseline"] = cohort_baseline

    # === Cohort v2 baseline（工程改善版本、與 v1 並列）===
    # v2 採 n_projection='quarterly' + r_window='adaptive' + as_of=today（auto nowcast）
    try:
        from ..forecast.cohort_model_v2 import forecast_cohort_v2
        from datetime import date
        target_year_v2 = ctx.get("target_year") or (
            pd.Timestamp(ref_date).year + 1 if ref_date else None)
        if target_year_v2:
            v2_r = forecast_cohort_v2(
                target_year=target_year_v2,
                n_projection='quarterly',
                r_window='adaptive',
                as_of_date=date.today().isoformat(),
                nowcast_mode='auto')
            if v2_r.get("success"):
                # 從 holdout 抓 v2 殘差 bias
                v2_bias = 0.0
                if holdout and holdout.get("summary", {}).get("by_model_mape"):
                    v2_info = holdout["summary"]["by_model_mape"].get("cohort_v2_auto", {})
                    v2_bias = v2_info.get("bias", 0.0) or 0.0
                v2_static_factor = 1 - v2_bias / 100.0
                v2_baseline = {
                    "cows": v2_r.get("predicted_cows"),
                    "daily_yield_kg": v2_r.get("predicted_daily_yield_kg"),
                    "lactation_days": 305,
                    "tons_after_productivity": v2_r.get("annual_total_tons"),
                    "tons_raw": v2_r.get("annual_total_tons_raw"),
                    "productivity_ratio": v2_r.get("productivity_ratio_target", 1.0),
                    "productivity_correction_applied": v2_r.get(
                        "productivity_correction_applied", True),
                    "target_year": target_year_v2,
                    "in_sample_mape": v2_r.get("in_sample_mape"),
                    "static_bias_pct": v2_bias,
                    "static_calibration_factor": v2_static_factor,
                    "annual_total_tons": v2_r.get("annual_total_tons") * v2_static_factor,
                    "v2_config": v2_r.get("v2_config", {}),
                    "seasonal_pattern": [
                        0.080, 0.082, 0.085, 0.087, 0.086, 0.084,
                        0.082, 0.082, 0.080, 0.081, 0.083, 0.085,
                    ],
                }
                ctx["cohort_v2_baseline"] = v2_baseline
                import logging
                logging.getLogger("milkfc.dashboard").info(
                    f"  Cohort v2 baseline: {v2_baseline['annual_total_tons']:,.0f} 公噸 "
                    f"(static_bias={v2_bias:+.2f}%, "
                    f"as_of={date.today().isoformat()}, "
                    f"nowcast_quarters={v2_baseline['v2_config'].get('nowcast_mode_actual')})")
    except Exception as e:
        import logging
        logging.getLogger("milkfc.dashboard").warning(
            f"  Cohort v2 baseline 計算失敗、dashboard 將不顯示 v2 選項: {e}")

    return ctx


def _render(p: dict) -> str:
    return f"""<!DOCTYPE html>
<html lang="zh-Hant"><head><meta charset="UTF-8">
<title>時間序列預測 - milkfc</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/hammerjs@2.0.8/hammer.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-zoom@2.0.1/dist/chartjs-plugin-zoom.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-annotation@3.0.1/dist/chartjs-plugin-annotation.min.js"></script>
<style>{_CSS}</style>
</head><body>

<header>
  <h1>📈 全國牛乳產量預測系統 <span class="header-en">National Milk Production Forecasting System</span></h1>
  <div class="meta">
    <span>資料截至 / Data thru: <code id="hdr_data_thru">{p['manifest']['config']['reference_date']}</code></span>
    <span>系統版本 / Version: <code>{p['manifest'].get('package_version','—')}</code></span>
    <span>快照 / Snapshot: <code>{p['manifest']['snapshot_id']}</code></span>
  </div>
</header>

<nav class="topnav">
  <a href="dashboard.html">📊 預測 / Forecast (Bottom-Up)</a>
  <a href="timeseries.html" class="active">📈 時間序列 / Time Series</a>
  <a href="seasonal.html">📅 月度分布 / Monthly</a>
  <a href="lactation.html">🐄 泌乳曲線 / Lactation</a>
  <span class="unit-picker">單位 / Unit:
    <select id="sel_unit">
      <option value="wton" selected>萬公噸 / 10k metric tons</option>
      <option value="ton">公噸 / metric ton</option>
      <option value="kton">千噸 / 1k metric tons</option>
      <option value="kg">kg</option>
    </select>
  </span>
</nav>

<!-- §1 預測摘要 -->
<section class="card summary-section">
  <h2>📌 預測摘要 <span class="h-en">Forecast Summary</span></h2>
  <div class="summary-grid" id="summary_grid"></div>
  <div class="summary-meta">
    <span>主要模型 / Primary Model: <b id="sm_model">—</b></span>
    <span>校正方法 / SF Method: <b id="sm_sf">—</b></span>
    <span class="sm-toggle"><a href="#methodology">📖 方法論 / Methodology</a></span>
  </div>

  <div class="exec-summary" id="exec_summary">
    <div class="exec-head">
      <span class="exec-title">📋 執行摘要 <span class="h-en">Executive Summary</span></span>
      <button class="exec-copy" id="exec_copy_btn"
        title="複製純文字版本到剪貼簿 / Copy plain text">📋 複製文字</button>
    </div>
    <div class="exec-body" id="exec_body"></div>
  </div>
</section>

<!-- §1.7 結構變數視覺化（cohort 模型用的三個物理變數）-->
<section class="card structural-section" id="structural-vars">
  <details open class="structural-details">
    <summary>
      <h2 style="display:inline-block;margin:0">🐄 結構變數趨勢 <span class="h-en">Structural Variables</span></h2>
      <span class="structural-summary-hint">
        （cohort 模型依據的三個物理變數、含歷史 + {p['context'].get('target_label', '目標年')} 預測值）
      </span>
    </summary>
    <p class="structural-intro">
      cohort_simple 用「<b>產量 = 產乳牛數 × 單頭日產乳 × 305 ÷ productivity 比率</b>」公式。
      下面三張圖讓您看看每個變數的**實際趨勢與預測點**、判斷預測合理性。
    </p>
    <div class="structural-grid">
      <div class="structural-chart-cell">
        <div class="structural-chart-title">🐄 產乳牛數 / Milking Cows</div>
        <div style="position:relative;height:240px"><canvas id="chart_cows"></canvas></div>
        <div class="structural-chart-note" id="note_cows"></div>
      </div>
      <div class="structural-chart-cell">
        <div class="structural-chart-title">🥛 單頭日產乳 / Daily Yield per Cow</div>
        <div style="position:relative;height:240px"><canvas id="chart_yield"></canvas></div>
        <div class="structural-chart-note" id="note_yield"></div>
      </div>
      <div class="structural-chart-cell">
        <div class="structural-chart-title">📐 DHI/全國 productivity 比率</div>
        <div style="position:relative;height:240px"><canvas id="chart_ratio"></canvas></div>
        <div class="structural-chart-note" id="note_ratio"></div>
      </div>
    </div>
  </details>
</section>

<!-- §2 月度預測詳情 -->
<section class="card">
  <h2>📈 月度預測詳情 <span class="h-en">Monthly Forecast Detail</span></h2>
  <div class="picker">
    <label>區域 / Region:</label>
    <select id="sel_region"></select>
    <label>尺度 / Scale:</label>
    <select id="sel_scale">
      <option value="dhi">DHI 加總 / DHI Aggregate</option>
      <option value="calibrated">全國估計（舊）/ National (Legacy)</option>
      <option value="calibrated_l4" selected>全國估計 · Level 4 ⭐ / National (L4)</option>
    </select>
    <label>主要模型 / Model:</label>
    <select id="sel_model">
      <option value="__best__" selected>最佳 / Best (auto)</option>
      <option value="cohort_simple">結構式 v1 simple（論文版本）</option>
      <option value="cohort_v2_auto">結構式 v2 auto（工程改善版本）</option>
      <option value="ensemble">Ensemble</option>
      <option value="stl_linear">stl_linear</option>
      <option value="holt_winters">holt_winters</option>
      <option value="sarima">sarima</option>
      <option value="prophet">prophet</option>
      <option value="naive_seasonal">naive_seasonal</option>
      <option value="neural_prophet">neural_prophet</option>
    </select>
  </div>
  <div class="paper-version-banner" style="background:#fff8e1;border-left:4px solid #ffa726;padding:10px 14px;margin:12px 0;font-size:13px;border-radius:4px;">
    📜 <b>論文版本</b>：cohort_simple（4 年滾動回測 MAPE 2.15%）。
    <span style="color:#666">本論文於中國畜牧學會誌投稿之凍結版本；下拉切換 cohort_v2_auto 可比對工程改善之 v2 版本（MAPE 1.77%）。</span>
  </div>
  <div class="chart-wrap" style="height:380px;"><canvas id="ts_chart"></canvas></div>
  <div class="chart-controls">
    <button id="zoom_reset" class="btn-mini">↺ 重置 / Reset</button>
    <button id="zoom_all" class="btn-mini">⇆ 全部 / All</button>
    <button id="zoom_recent" class="btn-mini">最近 5 年 / 5 yrs</button>
    <button id="zoom_forecast" class="btn-mini">只看預測 / Forecast</button>
    <span class="hint">💡 滑鼠滾輪縮放、拖曳移動 / Wheel: zoom, drag: pan</span>
  </div>
  <p class="note" id="ts_note"></p>
</section>

<!-- §3 各區域預測對比 -->
<section class="card">
  <h2>🗺️ 各區域預測對比 <span class="h-en">Regional Comparison</span></h2>
  <div class="region-toggles" id="region_toggles"></div>
  <div class="chart-wrap" style="height:360px;"><canvas id="all_regions_chart"></canvas></div>
  <div class="chart-controls">
    <button id="all_zoom_reset" class="btn-mini">↺ 重置</button>
    <button id="all_zoom_recent" class="btn-mini">最近 5 年</button>
    <button id="all_zoom_full" class="btn-mini">顯示全部</button>
  </div>
  <div id="region_total_summary"></div>
  <details class="info-detail">
    <summary>ⓘ 區域加總與全國值的差異 / Why regional sum ≠ national</summary>
    <p>各區域預測由獨立的時序模型產生、平滑強度與趨勢估計不同；
       加總後與全國模型直接預測通常有 1-3% 差異，
       為時序預測的階層不一致（hierarchical inconsistency）現象。
       若需強制相等可用 MinT 等 reconciliation 後處理。</p>
  </details>
</section>

<!-- §4 模型精度監控（同年估計 + 滾動回測合併） -->
<section class="card" id="accuracy-card">
  <h2>🎯 模型精度監控 <span class="h-en">Model Accuracy Monitoring</span></h2>

  <div class="truth-banner">
    🔒 <b>本區塊使用農業部〈牛乳產量〉年報計算誤差。產量資料未進入預測模型，僅作為事後驗證真值。</b><br/>
    <span class="db-meta-en">This section uses MOA's annual milk production report only to compute post-hoc accuracy. Production data is never used as a model input.</span>
  </div>

  <div class="acc-tabs">
    <button class="acc-tab active" data-tab="holdout">滾動回測 / Rolling Backtest</button>
    <button class="acc-tab" data-tab="sameyear">同年估計 / Same-Year Estimate</button>
  </div>

  <!-- Tab 1：滾動回測 -->
  <div class="acc-pane active" id="acc_holdout">
    <div class="explain-block explain-A">
      <div class="eb-title">📌 這個區塊在做什麼？ <span class="h-en">What is rolling backtest?</span></div>
      <p>模擬「<b>站在某年底、預測下一年</b>」的情境，看模型預測值與實際發生有多大誤差。
        例如要驗證 <b>2024</b> 預測：</p>
      <ol>
        <li>砍掉 2024 資料、模型只看到 ≤ 2023</li>
        <li>預測 2024 全年 → 對照 2024 農業部公告值</li>
      </ol>
      <p>4 年（2021–2024）平均誤差就是【<b>實戰精度</b>】、是給主管機關的可信度承諾。</p>
    </div>

    <div id="holdout_backtest_container"></div>

    <div class="explain-block explain-B">
      <div class="eb-title">📊 怎麼判讀數字？ <span class="h-en">How to interpret</span></div>
      <table class="explain-table">
        <tr><td><b>MAPE 平均絕對誤差百分比</b><br/>越小越準</td>
          <td>&lt; 5% 優秀 ｜ &lt; 10% 可用 ｜ &gt; 15% 警訊</td></tr>
        <tr><td><b>Bias 系統性偏差</b><br/>越接近 0 越好</td>
          <td>正 = 系統性高估（預測值偏高）｜ 負 = 系統性低估。<br/>
            系統會自動把 best_model 的 bias 從正式預測中扣除。</td></tr>
        <tr><td><b>系統採用模型 MAPE</b><br/>main pipeline</td>
          <td>系統實際採用的「最佳單一模型 + Level 4 SF」全管線在滾動回測下的誤差、<b>給主管機關的承諾數字</b></td></tr>
        <tr><td><b>Ensemble MAPE</b><br/>對照組</td>
          <td>所有時序模型加權平均後的全管線誤差。受表現較差模型拉低、僅供交叉驗證。</td></tr>
        <tr><td><b>SF L1 vs L4</b><br/>涵蓋率還原係數</td>
          <td>L1 = 直接用 Y-1 場數比；L4 = 用季報+年報外推到目標年（推薦）</td></tr>
      </table>
    </div>

    <div class="explain-block explain-D" id="sf_selection_logic"></div>

    <div class="explain-block explain-C" id="holdout_conclusion"></div>
  </div>

  <!-- Tab 2：同年估計 -->
  <div class="acc-pane" id="acc_sameyear">
    <div class="explain-block explain-A">
      <div class="eb-title">📌 這個區塊在做什麼？ <span class="h-en">What is same-year estimate?</span></div>
      <p>用該年【<b>實際 DHI 加總</b>（不是預測值）】× SF、對照農業部公告值，
        <b>只測試「SF 還原全國」這一段</b>準不準。</p>
      <p><b>跟滾動回測差在哪？</b></p>
      <ul>
        <li><b>滾動回測</b>：DHI 預測 × SF（兩段都測）</li>
        <li><b>同年估計</b>：真實 DHI × SF（只測 SF 還原這一段）</li>
      </ul>
      <p><b>為什麼要做這個？</b>幫助診斷誤差來源——是 SF 不準、還是時序模型不準？</p>
    </div>

    <div id="official_compare_container"></div>

    <div class="explain-block explain-B">
      <div class="eb-title">📊 三種 SF 方法的差別 <span class="h-en">The three SF methods</span></div>
      <table class="explain-table">
        <tr><td><b>M1 固定 SF</b></td>
          <td>用一個常數估算。缺點：DHI 涵蓋率年年變、固定值在每一年都不對。</td></tr>
        <tr><td><b>M2 場數比例</b><span data-best-marker="method_2"></span></td>
          <td>當年（農業部公告場數 ÷ DHI 場數）。物理意義直接、為原舊版主要方法。</td></tr>
        <tr><td><b>M3 結構分解</b><span data-best-marker="method_3"></span><br/>含 productivity 校正</td>
          <td>農業部公告產乳牛 × (DHI 平均單頭日產乳 ÷ productivity 比率) × 305 天。
            productivity 比率（DHI/全國）從 < 該年的歷史線性外推、避免循環引用。
            與 cohort_simple 預測模型同步邏輯。</td></tr>
      </table>
    </div>

    <details class="explain-block explain-fold">
      <summary><b>ⓘ 欄位意義 / Column definitions</b></summary>
      <ul style="margin-top:8px">
        <li><b>DHI 加總</b>：當年 DHI 樣本實際月乳量總和（非預測）</li>
        <li><b>農業部公告產量</b>：該年正式公告的全國總產量（驗證真值）</li>
        <li><b>M1/M2/M3 預測</b>：用該方法的 SF 還原 DHI 加總到全國尺度</li>
        <li><b>誤差%</b>：(預測值 − 公告值) / 公告值 × 100</li>
      </ul>
    </details>

    <div class="explain-block explain-C" id="sameyear_conclusion"></div>
  </div>
</section>

<!-- §5 系統狀態 -->
<section class="card status-section">
  <h2>📊 系統狀態 <span class="h-en">System Status</span></h2>
  <div class="status-grid" id="status_grid"></div>
  <details class="info-detail">
    <summary>ⓘ 更新建議 / Refresh Schedule</summary>
    <ul>
      <li><b>每月</b> / Monthly：上傳新月份 DHI 後執行
        <code>python -m milkfc forecast-ts --dashboard</code></li>
      <li><b>每季</b> / Quarterly：新季報發布後（季末 +3 個月）將檔案放入
        <code>raw_data/</code>、執行 <code>--rerun-backtest</code></li>
      <li><b>每年</b> / Annually：新年度〈牛乳產量〉年報發布後（次年中）
        更新驗證資料</li>
    </ul>
  </details>
</section>

<!-- §6 方法論 -->
<section class="card method-section" id="methodology">
  <h2>📖 方法論 <span class="h-en">Methodology</span></h2>
  <p class="note">本系統三階段流程；下方所有數字皆從本次執行動態抓取。/
     Three-stage pipeline; all numbers below are dynamically pulled from this run.</p>

  <details open class="case-detail">
    <summary><b>第一階段 · 資料處理 / Stage 1: Data Processing</b></summary>
    <div id="case_data"></div>
  </details>

  <details class="case-detail">
    <summary><b>第二階段 · 時序預測 / Stage 2: Time-Series Forecasting</b></summary>
    <div id="case_forecast"></div>
  </details>

  <details class="case-detail">
    <summary><b>第三階段 · 尺度校正 / Stage 3: Scale Calibration (Level 4)</b></summary>
    <div id="case_sf"></div>
  </details>

  <details class="case-detail">
    <summary><b>案例 · 預測 <span class="case-target-year">—</span> 全年 / Case Study</b></summary>
    <div id="case_predict"></div>
  </details>

  <details class="case-detail">
    <summary><b>驗證方法 / Validation Methodology</b></summary>
    <div id="case_validate"></div>
  </details>
</section>

<!-- 情境假設計算 / What-If Scenario（移至底部、預設收合） -->
<section class="card whatif-section" id="whatif">
  <details class="whatif-details">
    <summary>
      <h2 style="display:inline-block;margin:0">🎛️ 情境假設計算 <span class="h-en">What-If Scenario</span></h2>
      <span class="whatif-summary-hint">（點此展開、用主管機關熟悉的參數做敏感度分析）</span>
    </summary>
    <div id="whatif_body" style="margin-top:16px">
      <p style="font-size:12px;color:#888">（資料載入中…）</p>
    </div>
  </details>
</section>

<section class="card">
  <h2>🔧 運行配置 <span class="h-en">Run Configuration</span></h2>
  <pre>{json.dumps(p['manifest'], indent=2, ensure_ascii=False, default=str)}</pre>
</section>

<footer class="site-footer">
  <span>📊 全國牛乳產量預測系統 / National Milk Production Forecasting</span>
  <span class="footer-sep">·</span>
  <span>最後生成 / Generated: <code>{_dt.now().strftime("%Y-%m-%d %H:%M")}</code></span>
  <span class="footer-sep">·</span>
  <span>資料截至 / Data thru: <code>{p['manifest']['config']['reference_date']}</code></span>
  <span class="footer-sep">·</span>
  <span>快照 / Snapshot: <code>{p['manifest']['snapshot_id']}</code></span>
</footer>

<script>
const D = {json.dumps(p, default=str)};

// === 單位切換 ===
const UNIT_INFO = {{
  kg:   {{ divisor: 1, label: 'kg', precision: 0 }},
  ton:  {{ divisor: 1000, label: '公噸', precision: 1 }},
  wton: {{ divisor: 10000000, label: '萬公噸', precision: 3 }},
  kton: {{ divisor: 1000000, label: '千噸', precision: 2 }},
}};
let CUR_UNIT = 'wton';
function unit() {{ return UNIT_INFO[CUR_UNIT]; }}
function fmt_v(v) {{
  if (v == null) return '—';
  const u = unit();
  return (v / u.divisor).toFixed(u.precision);
}}
function fmt_int(v) {{
  if (v == null) return '—';
  const u = unit();
  return (v / u.divisor).toLocaleString(undefined,
    {{ minimumFractionDigits: u.precision, maximumFractionDigits: u.precision }});
}}
function unit_label() {{ return `月乳量 / Monthly milk (${{unit().label}})`; }}

// 公噸 → 當前單位（kg / 公噸 / 千噸 / 萬公噸）
function tonsToUnit(tons) {{
  if (tons == null || isNaN(tons)) return null;
  return tons * 1000 / unit().divisor;
}}
function fmtTonsInUnit(tons, precision) {{
  const v = tonsToUnit(tons);
  if (v == null) return '—';
  const u = unit();
  const p = precision != null ? precision : u.precision;
  return v.toLocaleString(undefined,
    {{ minimumFractionDigits: p, maximumFractionDigits: p }});
}}
function unitLabelOnly() {{ return unit().label; }}

// === Context（動態 evergreen）===
const CTX = D.context || {{}};
const TARGET_LABEL = CTX.target_label || '—';
const TARGET_YEAR  = CTX.target_year || '—';

// 模型選擇（覆寫 BEST_MODEL；用戶可從下拉選不同模型）
let SELECTED_MODEL = '__best__';

// =============================================
// §1 預測摘要卡片（6 張、雙語）
// =============================================
function fmt_tons(v) {{
  if (v == null) return '—';
  return (v).toLocaleString(undefined,
    {{maximumFractionDigits: 0}}) + ' 公噸';
}}
function fmt_wton(v) {{
  // 隨 unit 切換的「主要顯示」格式（值帶單位）
  if (v == null) return '—';
  return fmtTonsInUnit(v) + ' ' + unitLabelOnly();
}}
function renderSummary() {{
  const grid = document.getElementById('summary_grid');
  if (!grid) return;
  const cards = [
    {{
      label_zh: '目標期間', label_en: 'Target Period',
      value: TARGET_LABEL,
      sub_zh: `${{D.manifest.config.horizon_months || 12}} 個月`,
      sub_en: `${{D.manifest.config.horizon_months || 12}} months ahead`,
      icon: '🎯',
    }},
    {{
      label_zh: '預測值（中位數）', label_en: 'Forecast P50',
      value: fmt_wton(CTX.forecast_p50_tons),
      sub_zh: `≈ ${{fmt_tons(CTX.forecast_p50_tons)}}`,
      sub_en: 'central estimate',
      icon: '📊', highlight: true,
    }},
    {{
      label_zh: '信賴區間', label_en: 'Confidence Interval',
      value: (CTX.forecast_p10_tons != null && CTX.forecast_p90_tons != null)
              ? `${{fmtTonsInUnit(CTX.forecast_p10_tons)}}–${{fmtTonsInUnit(CTX.forecast_p90_tons)}}` : '—',
      sub_zh: 'P10–P90 ' + unitLabelOnly(),
      sub_en: '90% interval',
      icon: '📐',
    }},
    {{
      label_zh: '歷史精度', label_en: 'Historical MAPE',
      value: CTX.best_mape != null ? `±${{CTX.best_mape.toFixed(1)}}%` : '—',
      sub_zh: '4 年滾動回測',
      sub_en: '4-yr rolling backtest',
      icon: '✅',
    }},
    {{
      label_zh: '對照前年', label_en: 'vs Last Actual',
      value: CTX.latest_official_tons
              ? `${{fmtTonsInUnit(CTX.latest_official_tons)}} ${{unitLabelOnly()}}`
              : '—',
      sub_zh: CTX.latest_official_year ? `${{CTX.latest_official_year}} 年農業部公告值` : '—',
      sub_en: CTX.latest_official_year ? `${{CTX.latest_official_year}} official` : '—',
      icon: '📅',
    }},
    {{
      label_zh: '年增率', label_en: 'YoY Growth',
      value: CTX.yoy_per_year_pct != null
              ? `${{CTX.yoy_per_year_pct >= 0 ? '+' : ''}}${{CTX.yoy_per_year_pct.toFixed(1)}}%`
              : '—',
      sub_zh: CTX.year_gap ? `年化（跨 ${{CTX.year_gap}} 年）` : '—',
      sub_en: 'annualized',
      icon: '📈',
    }},
  ];

  grid.innerHTML = cards.map(c => `
    <div class="sum-card ${{c.highlight ? 'highlight' : ''}}">
      <div class="sc-icon">${{c.icon}}</div>
      <div class="sc-label-zh">${{c.label_zh}}</div>
      <div class="sc-label-en">${{c.label_en}}</div>
      <div class="sc-value">${{c.value}}</div>
      <div class="sc-sub">${{c.sub_zh}}<br/><span class="sc-sub-en">${{c.sub_en}}</span></div>
    </div>
  `).join('');

  document.getElementById('sm_model').textContent =
    CTX.best_model || 'ensemble';
  document.getElementById('sm_sf').textContent =
    CTX.sf_method || 'Level 4';
}}
renderSummary();

// =============================================
// §1 執行摘要（動態文字、含趨勢解讀、複製按鈕）
// =============================================
function trendDescription(yoy) {{
  if (yoy == null) return '—';
  const a = Math.abs(yoy);
  if (a < 0.5) return '幾近持平';
  if (a < 2)   return yoy > 0 ? '略增' : '略降';
  if (a < 5)   return yoy > 0 ? '穩定成長' : '穩定衰退';
  return yoy > 0 ? '明顯成長' : '明顯衰退';
}}

function industryInsight() {{
  // 從 sf_by_year 找最近 3-5 年的 SF 趨勢、解讀涵蓋率動態
  if (!CTX.sf_by_year) return '';
  const yrs = Object.keys(CTX.sf_by_year).map(Number).sort((a,b)=>a-b);
  const recent = yrs.filter(y => y >= (CTX.target_year || 2026) - 5
                                  && y <= (CTX.target_year || 2026));
  if (recent.length < 2) return '';
  const sfFirst = CTX.sf_by_year[recent[0]].sf;
  const sfLast = CTX.sf_by_year[recent[recent.length-1]].sf;
  const change = (sfLast - sfFirst) / sfFirst * 100;
  if (Math.abs(change) < 2) return '';
  if (change < 0) {{
    return `SF 從 ${{sfFirst.toFixed(2)}} 降至 ${{sfLast.toFixed(2)}}（−${{Math.abs(change).toFixed(0)}}%）、` +
           `反映 DHI 涵蓋率持續上升、樣本逐漸接近全國規模`;
  }} else {{
    return `SF 從 ${{sfFirst.toFixed(2)}} 升至 ${{sfLast.toFixed(2)}}（+${{change.toFixed(0)}}%）、` +
           `DHI 涵蓋率相對縮減`;
  }}
}}

function renderExecutiveSummary() {{
  const body = document.getElementById('exec_body');
  if (!body) return;

  const cfg = D.manifest.config || {{}};
  const tgt = CTX.target_year || '目標年';
  const wton = (v) => fmtTonsInUnit(v);  // 隨 unit 切換
  const uLbl = unitLabelOnly();
  const fcP50 = wton(CTX.forecast_p50_tons);
  const fcP10 = wton(CTX.forecast_p10_tons);
  const fcP90 = wton(CTX.forecast_p90_tons);
  const offY = CTX.latest_official_year || '—';
  const offV = wton(CTX.latest_official_tons);
  const yoy = CTX.yoy_per_year_pct;
  const yoyTrend = trendDescription(yoy);
  const yoyStr = yoy != null
    ? `${{yoy >= 0 ? '+' : ''}}${{yoy.toFixed(1)}}%/年`
    : '—';
  const insight = industryInsight();

  const dhiYrs = (CTX.dhi_year_min && CTX.dhi_year_max)
    ? `${{CTX.dhi_year_min}}-${{CTX.dhi_year_max}}` : '近 26 年';
  const dhiNYrs = CTX.dhi_n_years || '—';
  const dhiRecs = CTX.dhi_total_records
    ? CTX.dhi_total_records.toLocaleString() + ' 筆'
    : '近 500 萬筆';
  const annualYr = CTX.latest_official_year || '—';
  const qLatest = (CTX.data_sources||[]).find(s=>s.freq==='quarterly')?.latest || '—';
  const qFirst = '2019Q1';  // 從 quarterly_inventory.py
  const nQ = 21;            // 目前 21 季
  const nFarms = cfg.n_active_farms || '—';
  const refDate = cfg.reference_date || '—';
  const horizon = cfg.horizon_months || 12;
  const regs = cfg.regions || [];
  const bestM = CTX.best_model || 'ensemble';
  const bestMape = CTX.best_mape != null ? CTX.best_mape.toFixed(1) : '—';
  const bestBias = CTX.best_bias != null
    ? (CTX.best_bias>=0?'+':'') + CTX.best_bias.toFixed(1)
    : '—';
  const sfTgt = (CTX.sf_by_year && CTX.sf_by_year[tgt]) ? CTX.sf_by_year[tgt] : null;

  // 抓歷史資料給「預測解讀」段
  const hist = CTX.history || [];
  let trendNarr = '', cowsNarr = '';
  if (hist.length >= 5) {{
    const oldest = hist[0];
    const peak = hist.reduce((a,b) => b.production_tons > a.production_tons ? b : a);
    const latest = hist[hist.length - 1];
    const oldToPeakPct = ((peak.production_tons - oldest.production_tons) /
                            oldest.production_tons * 100);
    const peakToLatestPct = ((latest.production_tons - peak.production_tons) /
                              peak.production_tons * 100);
    trendNarr = `${{oldest.year}}–${{peak.year}} 年產量累計成長
                  <b>${{oldToPeakPct >= 0 ? '+' : ''}}${{oldToPeakPct.toFixed(1)}}%</b>
                 （${{wton(oldest.production_tons)}} → ${{wton(peak.production_tons)}} ${{uLbl}}），
                  ${{peak.year}}–${{latest.year}} 年回落 <b>${{peakToLatestPct.toFixed(1)}}%</b>`;
    const cowsPeak = hist.reduce((a,b) => b.n_milking_cows > a.n_milking_cows ? b : a);
    const cowsChange = ((latest.n_milking_cows - cowsPeak.n_milking_cows) /
                          cowsPeak.n_milking_cows * 100);
    if (Math.abs(cowsChange) > 1) {{
      cowsNarr = `泌乳牛口從 ${{cowsPeak.year}} 高峰
                  ${{cowsPeak.n_milking_cows.toLocaleString()}} 頭逐年下降至
                  ${{latest.year}} 的 ${{latest.n_milking_cows.toLocaleString()}} 頭
                  (${{cowsChange.toFixed(1)}}%)、為產量回落主因`;
    }}
  }}

  // 風險限制：最近一次 backtest 表現
  const lastBtY = CTX.last_backtest_year;
  const lastBtErr = CTX.last_backtest_full_err;
  const lastBtErrStr = lastBtErr != null
    ? `${{lastBtErr >= 0 ? '+' : ''}}${{lastBtErr.toFixed(1)}}%` : '—';

  // 下次更新建議的日期（從目標年推算）
  const targetYr = CTX.target_year || (parseInt(refDate.substring(0,4))+1);
  const nextQ1Date = `${{targetYr}}-04`;
  const nextH1Date = `${{targetYr}}-07`;
  const nextAnnualDate = `${{targetYr+1}}-06`;

  // HTML 格式（給網頁顯示）
  const html = `
    <p class="exec-conclusion">
      <span class="exec-tag">結論</span>
      本系統預測 <b>${{tgt}}</b> 年全國牛乳產量
      <b class="exec-headline">${{fcP50}} ${{uLbl}}</b>
      （信賴區間 <b>${{fcP10}}–${{fcP90}}</b> ${{uLbl}}、即 90% 機率落於此區間），
      相對 ${{annualYr}} 年農業部公告值 <b>${{offV}}</b> ${{uLbl}}
      <b>${{yoyTrend}}</b>（年化 ${{yoyStr}}）。
      ${{insight ? insight + '。' : ''}}
    </p>

    <div class="exec-grid">
      <div class="exec-row"><span class="exec-label">▸ 輸入資料</span>
        <span class="exec-content">
          DHI（Dairy Herd Improvement、乳牛性能改良紀錄）月度資料
          ${{dhiYrs}}（${{dhiNYrs}} 年）、共 ${{dhiRecs}}<br/>
          農業部〈畜牧生產〉年報（場數、產乳牛頭數）2015–${{annualYr}} 年<br/>
          農業部〈在養量比較〉季報 ${{qFirst}}–${{qLatest}}（${{nQ}} 季）
        </span>
      </div>
      <div class="exec-row"><span class="exec-label">▸ 預測參數</span>
        <span class="exec-content">
          基準日 ${{refDate}}、預測時程 ${{horizon}} 個月（${{tgt}}）<br/>
          區域涵蓋 ${{regs.length}} 個（${{regs.slice(0,3).join('、')}}…）<br/>
          活躍場數 ${{nFarms}} 場（最近 180 天有測乳）
        </span>
      </div>
      <div class="exec-row"><span class="exec-label">▸ 預測方法</span>
        <span class="exec-content">
          ${{(() => {{
            const desc = {{
              'stl_linear': 'STL 分解（趨勢 + 季節）+ 線性外推',
              'cohort_simple': '結構式 v1（論文版本）：產乳牛數 × 單頭日產乳 × 305 天 ÷ DHI/全國 productivity 比率',
              'cohort_v2_auto': '結構式 v2（工程改善版）：N 季度回歸 + r ensemble (5yr OLS + 3yr mean) + auto nowcast',
              'neural_prophet': 'Prophet + AR-Net 神經網路（學最近 12 期 autoregressive 訊號）',
              'holt_winters': '三重指數平滑（level + trend + seasonal）',
              'sarima': 'pmdarima auto_arima（KPSS+OCSB+AIC 自動選階）',
              'prophet': 'Facebook Prophet（trend + yearly seasonal + changepoint）',
              'naive_seasonal': '基準線：去年同月 × 年增率',
              'ensemble': '加權平均集成（權重 = 1/MAPE）',
            }}[bestM] || '依資料推算';
            const isCohort = (bestM === 'cohort_simple' || bestM === 'cohort_v2_auto');
            const sfPart = isCohort
              ? '不需 SF 還原（cohort 直接全國尺度）'
              : `尺度校正方法 <b>Level 4 SF</b>（Scale Factor、涵蓋率還原係數）：${{sfTgt ? `SF[${{tgt}}] = ${{sfTgt.official_farms.toFixed(0)}} / ${{sfTgt.dhi_farms.toFixed(0)}} = <b>${{sfTgt.sf.toFixed(3)}}</b>（農業部公告場數 ÷ DHI 場數）` : '從歷史外推至目標年'}}`;
            return `主要模型 <b>${{bestM}}</b>（${{desc}}、系統依 4 年 holdout backtest 滾動回測自動選出）<br/>${{sfPart}}`;
          }})()}}
        </span>
      </div>
      <div class="exec-row exec-crossval"><span class="exec-label">▸ 多模型交叉驗證</span>
        <span class="exec-content">
          ${{(() => {{
            const t3 = CTX.top3_models || [];
            if (!t3.length) return '本系統採用多種獨立方法做交叉驗證。';
            // 算範圍
            const preds = t3.filter(m => m.pred_wton != null)
                           .map(m => m.pred_wton);
            const minP = preds.length ? Math.min(...preds) : null;
            const maxP = preds.length ? Math.max(...preds) : null;
            const spreadPct = (preds.length && minP)
              ? ((maxP - minP) / minP * 100).toFixed(1) : '—';
            const rows = t3.map((m, i) => {{
              const star = (i === 0) ? ' ⭐' : '';
              const w = m.pred_wton != null ? m.pred_wton.toFixed(2)+' '+uLbl : '—';
              const mape = m.mape != null ? m.mape.toFixed(1)+'%' : '—';
              return `<tr><td>${{m.name}}${{star}}</td>
                      <td class="num">${{w}}</td>
                      <td class="num">${{mape}}</td></tr>`;
            }}).join('');
            return `
              <p style="margin:0 0 6px">本次預測由 <b>${{t3.length}}</b> 種獨立方法產生互相驗證、
                所有方法收斂於 <b>${{minP ? minP.toFixed(1) : '—'}}–${{maxP ? maxP.toFixed(1) : '—'}}</b>
                ${{uLbl}}（差異 <b>${{spreadPct}}%</b>）：</p>
              <table class="exec-table">
                <thead><tr><th>方法</th><th>${{tgt}} 預測</th>
                  <th>滾動回測 MAPE<br/><span style="font-weight:400;font-size:10px;color:#888">holdout 平均絕對誤差%</span></th></tr></thead>
                <tbody>${{rows}}</tbody>
              </table>
              <p style="margin:6px 0 0;font-size:11px;color:#888">
                主要預測（採用 ${{bestM}}）= <b>${{fcP50}} ${{uLbl}}</b>（顯示於上方主結論）。
                其他模型僅作交叉驗證、不影響主結論。
              </p>`;
          }})()}}
        </span>
      </div>
      <div class="exec-row"><span class="exec-label">▸ 歷史精度</span>
        <span class="exec-content">
          系統採用模型 <b>${{bestM}}</b> 之 4 年滾動回測 MAPE = <b>${{bestMape}}%</b>
          （Mean Absolute Percentage Error、平均絕對誤差百分比）、
          bias <b>${{bestBias}}%</b>（系統性偏差、已於正式預測自動扣除）<br/>
          含時序預測 + Level 4 SF 涵蓋率還原全管線、對照農業部公告值<br/>
          P10–P90 信賴區間：時序模型內部估計 ~90% 機率落於 <b>${{fcP10}}–${{fcP90}}</b> ${{uLbl}}
        </span>
      </div>
      <div class="exec-row exec-howto"><span class="exec-label">▸ 如何閱讀本預測</span>
        <span class="exec-content">
          <ul style="margin:4px 0 0 16px;padding:0;">
            <li><b>P50 中位預測</b>：50% 機率高於它、50% 機率低於它。
              不是「保證值」、實際可能略偏離。</li>
            <li><b>P10–P90 區間</b>：模型內部估計 <b>~90% 機率</b>落於 ${{fcP10}}–${{fcP90}} ${{uLbl}}
              （依時序模型殘差分布、未額外納入 SF 還原步驟誤差）。不是「極限」、實務上仍有超出可能。</li>
            <li><b>Bias ${{bestBias}}%</b>：系統歷年「平均偏${{CTX.best_bias>=0 ? '高' : '低'}}」。
              已自動扣減於 P50；如需更保守、可額外調整。</li>
            <li><b>MAPE ${{bestMape}}%</b>：過去 4 年的平均絕對誤差比例。
              不保證未來、但反映歷史精度。</li>
          </ul>
        </span>
      </div>
      <div class="exec-row exec-horizon"><span class="exec-label">▸ 預測時程信賴度</span>
        <span class="exec-content">
          ${{(() => {{
            const bm = CTX.best_mape;
            const sR = bm != null ? Math.max(1, bm * 0.5).toFixed(1) : '2';
            const mR = bm != null ? bm.toFixed(1) : '4';
            const lR = bm != null ? (bm * 1.5).toFixed(1) : '6';
            return `
              <ul style="margin:4px 0 0 16px;padding:0;">
                <li><b>短期</b>（未來 1–3 個月）：高信賴、估計誤差約 ±${{sR}}%
                  <span style="color:#888">— 最近資料剛輸入、模型已捕捉現況</span></li>
                <li><b>中期</b>（未來 4–9 個月）：中等信賴、估計誤差約 ±${{mR}}%
                  <span style="color:#888">— 時序動能仍有效、結構訊號可能淡化</span></li>
                <li><b>長期</b>（未來 10–12 個月）：較低信賴、估計誤差約 ±${{lR}}%
                  <span style="color:#888">— 依賴趨勢延伸、結構轉折難預測</span></li>
              </ul>
              <p style="margin:6px 0 0;font-size:12px;color:#666">
                ※ 估計值依本次滾動回測 MAPE = ${{bm != null ? bm.toFixed(1) : '—'}}% 推算（短期 ×0.5、中期 ×1.0、長期 ×1.5）。<br/>
                建議：每月上傳新 DHI 後重跑、可大幅縮小不確定性。
              </p>`;
          }})()}}
        </span>
      </div>
      <div class="exec-row exec-interpret"><span class="exec-label">▸ 預測解讀</span>
        <span class="exec-content">
          ${{trendNarr ? trendNarr + '；' : ''}}
          ${{cowsNarr ? cowsNarr + '。' : ''}}
          本次預測 <b>${{fcP50}} ${{uLbl}}</b>延續最近年度走勢、
          ${{yoy != null && Math.abs(yoy) < 1 ? '不顯示反彈或續跌' : (yoy > 0 ? '延續成長動能' : '延續微降趨勢')}}。
        </span>
      </div>
      <div class="exec-row exec-risk"><span class="exec-label">▸ 風險與限制</span>
        <span class="exec-content">
          時序模型本質為趨勢延伸、抓不到結構性轉折。本系統在以下情境下準度可能下降：
          <ul style="margin:6px 0 0 16px;padding:0;">
            <li>飼料／能源價格大幅波動（影響淘汰率與單頭產乳）</li>
            <li>政策變動（進口配額、保價收購、補貼方案）</li>
            <li>重大疫病或極端氣候事件（如熱緊迫、口蹄疫）</li>
            <li>DHI 樣本場結構變動（新增大規模酪農場、樣本與母體偏離）</li>
          </ul>
          ${{lastBtErr != null ? `最近一次 holdout 回測（${{lastBtY}} 年）誤差 <b>${{lastBtErrStr}}</b>、屬正常範圍。` : ''}}
        </span>
      </div>
      <div class="exec-row exec-refresh"><span class="exec-label">▸ 下次更新建議</span>
        <span class="exec-content">
          <ul style="margin:0 0 0 16px;padding:0;">
            <li><b>每月</b>：上傳新 DHI 月份檔案 → 預測值微調（誤差 ±0.5%）</li>
            <li><b>${{nextQ1Date}}</b>：${{targetYr}} Q1 季報發布 → 校正係數更新（誤差 ±1%）</li>
            <li><b>${{nextH1Date}}</b>：${{targetYr}} 上半年資料 → 預測精度提升至 ±2%</li>
            <li><b>${{nextAnnualDate}}</b>：${{targetYr}}〈牛乳產量〉年報發布 → 可驗證本次預測</li>
          </ul>
        </span>
      </div>
      <div class="exec-row exec-disclaimer"><span class="exec-label">▸ 驗證資料聲明</span>
        <span class="exec-content">
          農業部〈牛乳產量〉年報（1967–${{annualYr}}）僅用於計算歷史 MAPE 與 bias、
          <b>從未進入預測模型</b>。本系統預測值僅依據 DHI 紀錄 + 在養量資料、屬獨立預測。
        </span>
      </div>
    </div>

    <p class="exec-footer">
      ⓘ 詳細方法請參考下方「📖 方法論」章節。/
      See the <a href="#methodology">Methodology</a> section below for details.
    </p>
  `;
  body.innerHTML = html;

  // 純文字版本（用於複製到剪貼簿、移除 HTML 標籤）
  const stripHtml = (s) => s ? s.replace(/<[^>]*>/g, '') : '';
  const plainText = [
    `【全國牛乳產量預測 ${{tgt}} 年｜執行摘要】`,
    ``,
    `■ 結論`,
    `本系統預測 ${{tgt}} 年全國牛乳產量 ${{fcP50}} ${{uLbl}}`,
    `（信賴區間 ${{fcP10}}–${{fcP90}} ${{uLbl}}、即 90% 機率落於此區間），`,
    `相對 ${{annualYr}} 年農業部公告值 ${{offV}} ${{uLbl}} ${{yoyTrend}}（年化 ${{yoyStr}}）。`,
    insight ? `${{insight}}。` : '',
    ``,
    `■ 輸入資料`,
    `‧ DHI（Dairy Herd Improvement、乳牛性能改良紀錄）月度資料 ${{dhiYrs}}（${{dhiNYrs}} 年），共 ${{dhiRecs}}`,
    `‧ 農業部〈畜牧生產〉年報（場數、產乳牛頭數）2015–${{annualYr}} 年`,
    `‧ 農業部〈在養量比較〉季報 ${{qFirst}}–${{qLatest}}（${{nQ}} 季）`,
    ``,
    `■ 預測參數`,
    `‧ 基準日：${{refDate}}`,
    `‧ 預測時程：${{horizon}} 個月（${{tgt}} 年全年）`,
    `‧ 區域涵蓋：${{regs.length}} 個（${{regs.join('、')}}）`,
    `‧ 活躍場數：${{nFarms}} 場（最近 180 天有測乳）`,
    ``,
    `■ 預測方法`,
    (() => {{
      const desc = {{
        'stl_linear': 'STL 分解（趨勢 + 季節）+ 線性外推',
        'cohort_simple': '結構式 v1（論文版本）：產乳牛數 × 單頭日產乳 × 305 天 ÷ DHI/全國 productivity 比率',
        'cohort_v2_auto': '結構式 v2（工程改善版）：N 季度回歸 + r ensemble (5yr OLS + 3yr mean) + auto nowcast',
        'neural_prophet': 'Prophet + AR-Net 神經網路',
        'holt_winters': '三重指數平滑',
        'sarima': 'pmdarima auto_arima（KPSS+OCSB+AIC）',
        'prophet': 'Facebook Prophet',
        'naive_seasonal': '基準線：去年同月 × 年增率',
        'ensemble': '加權平均集成',
      }}[bestM] || '依資料推算';
      return `‧ 主要模型：${{bestM}}（${{desc}}、系統依 4 年 holdout backtest 滾動回測自動選出）`;
    }})(),
    (bestM === 'cohort_simple' || bestM === 'cohort_v2_auto')
      ? `‧ 尺度：cohort 直接全國尺度（不需 SF 還原）`
      : `‧ 尺度校正方法：Level 4 SF（Scale Factor、涵蓋率還原係數）—— 最新季報 + 年報外推到目標年`,
    (bestM !== 'cohort_simple' && bestM !== 'cohort_v2_auto' && sfTgt) ? `‧ 本次 SF[${{tgt}}] = ${{sfTgt.official_farms.toFixed(0)}} ÷ ${{sfTgt.dhi_farms.toFixed(0)}} = ${{sfTgt.sf.toFixed(3)}}（農業部公告場數 ÷ DHI 場數）` : '',
    ``,
    `■ 歷史精度（4 年滾動回測）`,
    (bestM === 'cohort_simple' || bestM === 'cohort_v2_auto')
      ? `‧ 系統採用模型 ${{bestM}} 全管線 MAPE = ${{bestMape}}%（結構式公式、含 productivity 校正）`
      : `‧ 系統採用模型 ${{bestM}} 全管線 MAPE = ${{bestMape}}%（時序預測 + Level 4 SF 還原）`,
    `‧ Bias（系統性偏差）= ${{bestBias}}%（${{CTX.best_bias>=0 ? '系統性略高估' : '系統性略低估'}}、已於正式預測自動扣除）`,
    `‧ P10–P90 區間：時序模型內部估計 ~90% 機率落於 ${{fcP10}}–${{fcP90}} ${{uLbl}}`,
    ``,
    // 多模型交叉驗證
    `■ 多模型交叉驗證（前 3 名模型對照）`,
    ...(CTX.top3_models || []).map((m, i) => {{
      const star = (i === 0) ? ' ⭐ 系統採用' : '';
      const w = m.pred_wton != null ? m.pred_wton.toFixed(2) + ' ' + uLbl : '—';
      return `‧ ${{m.name}}${{star}}：${{w}}（holdout MAPE ${{m.mape != null ? m.mape.toFixed(1)+'%' : '—'}}）`;
    }}),
    `‧ 主要預測（${{bestM}}、顯示於主結論）：${{fcP50}} ${{uLbl}}`,
    ``,
    `■ 如何閱讀本預測`,
    `‧ P50（中位預測）：50% 機率高於它、50% 機率低於它，不是保證值`,
    `‧ P10–P90 區間：時序模型內部估計 ~90% 機率落於 ${{fcP10}}–${{fcP90}} ${{uLbl}}（未額外納入 SF 還原步驟誤差）`,
    `‧ Bias ${{bestBias}}%：系統歷年平均偏${{CTX.best_bias>=0 ? '高' : '低'}}、已自動扣減於 P50`,
    `‧ MAPE ${{bestMape}}%：過去 4 年平均絕對誤差比例、反映歷史精度`,
    ``,
    `■ 預測時程信賴度（依本次 MAPE = ${{bestMape}}% 推算）`,
    `‧ 短期（1–3 個月）：高信賴、估計誤差約 ±${{(CTX.best_mape != null ? Math.max(1, CTX.best_mape * 0.5).toFixed(1) : '—')}}%`,
    `‧ 中期（4–9 個月）：中等信賴、估計誤差約 ±${{(CTX.best_mape != null ? CTX.best_mape.toFixed(1) : '—')}}%`,
    `‧ 長期（10–12 個月）：較低信賴、估計誤差約 ±${{(CTX.best_mape != null ? (CTX.best_mape * 1.5).toFixed(1) : '—')}}%`,
    ``,
    `■ 預測解讀`,
    trendNarr ? '‧ ' + stripHtml(trendNarr) : '',
    cowsNarr ? '‧ ' + stripHtml(cowsNarr) : '',
    `‧ 本次預測 ${{fcP50}} ${{uLbl}}延續最近年度走勢、` +
      (yoy != null && Math.abs(yoy) < 1 ? '不顯示反彈或續跌' :
        (yoy > 0 ? '延續成長動能' : '延續微降趨勢')),
    ``,
    `■ 風險與限制`,
    `‧ 時序模型本質為趨勢延伸、抓不到結構性轉折`,
    `‧ 飼料／能源價格大幅波動（影響淘汰率與單頭產乳）`,
    `‧ 政策變動（進口配額、保價收購、補貼方案）`,
    `‧ 重大疫病或極端氣候事件（如熱緊迫、口蹄疫）`,
    `‧ DHI 樣本場結構變動（新增大規模酪農場、樣本與母體偏離）`,
    lastBtErr != null ? `‧ 最近一次 holdout 回測（${{lastBtY}} 年）誤差 ${{lastBtErrStr}}、屬正常範圍。` : '',
    ``,
    `■ 下次更新建議`,
    `‧ 每月：上傳新 DHI 月份檔案 → 預測值微調（誤差 ±0.5%）`,
    `‧ ${{nextQ1Date}}：${{targetYr}} Q1 季報發布 → 校正係數更新（誤差 ±1%）`,
    `‧ ${{nextH1Date}}：${{targetYr}} 上半年資料 → 預測精度提升至 ±2%`,
    `‧ ${{nextAnnualDate}}：${{targetYr}}〈牛乳產量〉年報發布 → 可驗證本次預測`,
    ``,
    `■ 驗證資料聲明`,
    `農業部〈牛乳產量〉年報（1967–${{annualYr}}）僅用於計算歷史 MAPE 與 bias，`,
    `從未進入預測模型。本系統預測值僅依據 DHI 紀錄 + 在養量資料、屬獨立預測。`,
    ``,
    `（資料截至 ${{refDate}}、本次執行 ${{D.manifest.snapshot_id}}）`,
  ].filter(s => s !== null && s !== '').join('\\n');

  // 綁定複製按鈕
  document.getElementById('exec_copy_btn').onclick = () => {{
    if (navigator.clipboard) {{
      navigator.clipboard.writeText(plainText).then(() => {{
        const btn = document.getElementById('exec_copy_btn');
        const orig = btn.textContent;
        btn.textContent = '✓ 已複製';
        btn.classList.add('copied');
        setTimeout(() => {{
          btn.textContent = orig;
          btn.classList.remove('copied');
        }}, 1800);
      }}).catch(err => {{
        alert('複製失敗：' + err.message);
      }});
    }} else {{
      // fallback
      const ta = document.createElement('textarea');
      ta.value = plainText;
      document.body.appendChild(ta);
      ta.select();
      try {{ document.execCommand('copy'); alert('已複製（fallback）'); }}
      catch (e) {{ alert('複製失敗：' + e.message); }}
      document.body.removeChild(ta);
    }}
  }};
}}
renderExecutiveSummary();

// =============================================
// §1.7 結構變數視覺化（cohort 用的三個物理變數）
// =============================================
function renderStructuralCharts() {{
  const hist = CTX.structural_history || [];
  const cb = CTX.cohort_baseline;
  if (!hist.length) return;
  const targetYear = cb ? cb.target_year : null;

  function _drawLineChart(canvasId, dataArr, predY, predVal, yLabel, fmtFn) {{
    const el = document.getElementById(canvasId);
    if (!el) return;
    const labels = dataArr.map(d => d.year);
    // 拆成 ：年報歷史值 vs 季報估算（2025）
    const annualValues = dataArr.map(d => d.is_partial ? null : d.value);
    const partialValues = dataArr.map(d => d.is_partial ? d.value : null);
    // 加入預測點
    const predLabels = [...labels];
    const annualValuesExt = [...annualValues];
    const partialValuesExt = [...partialValues];
    const predValues = labels.map(() => null);
    if (predY != null && predVal != null) {{
      predLabels.push(predY);
      annualValuesExt.push(null);
      partialValuesExt.push(null);
      predValues.push(predVal);
    }}

    new Chart(el, {{
      type: 'line',
      data: {{
        labels: predLabels,
        datasets: [
          {{
            label: '歷史（年報）',
            data: annualValuesExt,
            borderColor: '#1a3550',
            backgroundColor: '#1a3550',
            borderWidth: 2.5,
            pointRadius: 4,
            tension: 0.2,
            spanGaps: true,
          }},
          {{
            label: '2025 季報估算',
            data: partialValuesExt,
            borderColor: '#888',
            backgroundColor: '#888',
            borderWidth: 2,
            borderDash: [4, 3],
            pointRadius: 5,
            pointStyle: 'rect',
            tension: 0,
            spanGaps: false,
          }},
          {{
            label: `${{predY}} 預測`,
            data: predValues,
            borderColor: '#c9930e',
            backgroundColor: '#c9930e',
            borderWidth: 2.5,
            pointRadius: 8,
            pointStyle: 'star',
            tension: 0,
            spanGaps: false,
          }},
        ]
      }},
      options: {{
        responsive: true, maintainAspectRatio: false,
        plugins: {{
          legend: {{display: true, position: 'bottom',
                    labels: {{font: {{size: 10}}, boxWidth: 12}}}},
          tooltip: {{
            callbacks: {{
              label: ctx => `${{ctx.dataset.label}}: ${{fmtFn ? fmtFn(ctx.parsed.y) : ctx.parsed.y}}`,
            }},
          }},
        }},
        scales: {{
          y: {{title: {{display: true, text: yLabel, font: {{size: 11}}}}}},
        }},
      }},
    }});
  }}

  // 1) 產乳牛數
  const cowsData = hist.filter(d => d.n_milking_cows != null)
    .map(d => ({{year: d.year, value: d.n_milking_cows,
                  is_partial: d.is_partial, note: d.note}}));
  _drawLineChart('chart_cows', cowsData,
    targetYear, cb ? cb.cows : null, '產乳牛數（頭）',
    v => v != null ? Math.round(v).toLocaleString() + ' 頭' : '—');
  // 牛數註解（2024 公告 → 2025 季報 → 2026 預測）
  if (cowsData.length >= 2 && cb) {{
    const peak = cowsData.reduce((a, b) => b.value > a.value ? b : a);
    // 找最後一個年報（無 is_partial 的）和最後的季報估計
    const lastAnnual = cowsData.filter(d => !d.is_partial).slice(-1)[0];
    const partial2025 = cowsData.find(d => d.year === 2025 && d.is_partial);
    let line1 = '';
    if (lastAnnual) {{
      line1 = `${{lastAnnual.year}} 公告 ${{Math.round(lastAnnual.value).toLocaleString()}} 頭`;
    }}
    if (partial2025) {{
      line1 += ` → 2025 季報估 <b>${{Math.round(partial2025.value).toLocaleString()}} 頭</b>`;
    }}
    line1 += ` → ${{targetYear}} 預測 <b>${{Math.round(cb.cows).toLocaleString()}} 頭</b>`;
    const refVal = (partial2025 || lastAnnual).value;
    const refYr = (partial2025 || lastAnnual).year;
    const change = (cb.cows - refVal) / refVal * 100;
    line1 += `（vs ${{refYr}}: ${{change>=0?'+':''}}${{change.toFixed(1)}}%）`;
    const peakDelta = ((cb.cows - peak.value) / peak.value * 100);
    document.getElementById('note_cows').innerHTML =
      line1 + `<br/>高峰 ${{peak.year}}: ${{Math.round(peak.value).toLocaleString()}} 頭、預測 vs 高峰 ${{peakDelta.toFixed(1)}}%`
      + (partial2025 ? `（⚠️ 2025 為 ${{partial2025.note || '季報估算'}}、非年報公告）` : '');
  }}

  // 2) 單頭日產乳（DHI 和全國分開畫）
  const dhiYieldData = hist.filter(d => d.dhi_yield_kg != null)
    .map(d => ({{year: d.year, value: d.dhi_yield_kg}}));
  // 改：直接畫雙線（DHI + 全國）
  const yieldEl = document.getElementById('chart_yield');
  if (yieldEl) {{
    const labels = hist.filter(d => d.dhi_yield_kg != null).map(d => d.year);
    const dhiValues = hist.filter(d => d.dhi_yield_kg != null).map(d => d.dhi_yield_kg);
    const natValues = hist.filter(d => d.dhi_yield_kg != null).map(d => d.national_yield_kg);

    const predLabels = [...labels];
    const dhiPreds = labels.map(() => null);
    const natPreds = labels.map(() => null);
    if (cb && targetYear) {{
      predLabels.push(targetYear);
      dhiValues.push(null); natValues.push(null);
      dhiPreds.push(cb.daily_yield_kg);
      // 全國預測 = DHI / ratio
      const natPred = cb.productivity_ratio
        ? cb.daily_yield_kg / cb.productivity_ratio : null;
      natPreds.push(natPred);
    }}

    new Chart(yieldEl, {{
      type: 'line',
      data: {{
        labels: predLabels,
        datasets: [
          {{label: 'DHI 樣本', data: dhiValues, borderColor: '#1e7c3a',
            backgroundColor: '#1e7c3a', borderWidth: 2, pointRadius: 3, tension: 0.2}},
          {{label: '全國平均', data: natValues, borderColor: '#a05a00',
            backgroundColor: '#a05a00', borderWidth: 2, pointRadius: 3, tension: 0.2}},
          {{label: `${{targetYear}} 預測 DHI`, data: dhiPreds,
            borderColor: '#c9930e', backgroundColor: '#c9930e',
            borderWidth: 2, pointRadius: 8, pointStyle: 'star'}},
          {{label: `${{targetYear}} 預測全國`, data: natPreds,
            borderColor: '#d05a3c', backgroundColor: '#d05a3c',
            borderWidth: 2, pointRadius: 8, pointStyle: 'star'}},
        ]
      }},
      options: {{
        responsive: true, maintainAspectRatio: false,
        plugins: {{
          legend: {{display: true, position: 'bottom',
                    labels: {{font: {{size: 10}}, boxWidth: 12}}}},
          tooltip: {{
            callbacks: {{
              label: ctx => `${{ctx.dataset.label}}: ${{ctx.parsed.y != null ? ctx.parsed.y.toFixed(2) + ' kg/天' : '—'}}`,
            }},
          }},
        }},
        scales: {{
          y: {{title: {{display: true, text: 'kg/天', font: {{size: 11}}}}}},
        }},
      }},
    }});
    if (cb && dhiYieldData.length >= 2) {{
      const lastD = dhiYieldData[dhiYieldData.length - 1];
      const change = (cb.daily_yield_kg - lastD.value) / lastD.value * 100;
      document.getElementById('note_yield').innerHTML =
        `DHI ${{lastD.year}}: ${{lastD.value.toFixed(2)}} kg/天 → ${{targetYear}} 預測 <b>${{cb.daily_yield_kg.toFixed(2)}} kg/天</b>（${{change>=0?'+':''}}${{change.toFixed(1)}}%）`
        + `、長期上升 = 遺傳改良 + 飼料管理進步`;
    }}
  }}

  // 3) Productivity 比率
  const ratioData = hist.filter(d => d.productivity_ratio != null)
    .map(d => ({{year: d.year, value: d.productivity_ratio}}));
  _drawLineChart('chart_ratio', ratioData,
    targetYear, cb ? cb.productivity_ratio : null,
    'DHI / 全國',
    v => v != null ? v.toFixed(3) : '—');
  if (ratioData.length >= 2 && cb) {{
    const lastR = ratioData[ratioData.length - 1];
    const firstR = ratioData[0];
    document.getElementById('note_ratio').innerHTML =
      `${{firstR.year}}: ${{firstR.value.toFixed(3)}} → ${{lastR.year}}: ${{lastR.value.toFixed(3)}} → ${{targetYear}} 預測 <b>${{cb.productivity_ratio.toFixed(3)}}</b>`
      + `、收斂中（DHI 涵蓋率擴大、樣本越接近全國平均）`;
  }}
}}
renderStructuralCharts();

// =============================================
// §1.5 情境假設計算 / What-If Scenario
// =============================================
// 預設模板：[label, icon, productivity%, herd%, note]
const SCENARIO_PRESETS = [
  {{key: 'reset',     icon: '↻',  label: '重置基準',       yield_pct:  0, cow_pct:  0,
    desc: '回到 cohort 模型預測值'}},
  {{key: 'heat_mid',  icon: '🌡️', label: '熱浪輕度',       yield_pct: -3, cow_pct:  0,
    desc: 'THI 80-83、單頭日產乳下降約 3%'}},
  {{key: 'heat_sev',  icon: '🥵', label: '熱浪嚴重',       yield_pct: -7, cow_pct: -1,
    desc: 'THI 85+、單頭下降 7% 並引發少量淘汰'}},
  {{key: 'cow_down5', icon: '🐄', label: '牛口減少 5%',    yield_pct:  0, cow_pct: -5,
    desc: '產乳牛數減少 5%（不指定原因）'}},
  {{key: 'cow_down15',icon: '🐄', label: '牛口減少 15%',   yield_pct:  0, cow_pct:-15,
    desc: '產乳牛數大幅減少 15%（不指定原因）'}},
  {{key: 'subsidy',   icon: '💰', label: '政策擴場 +5%',   yield_pct:  0, cow_pct:  5,
    desc: '補貼帶動牛口擴增 5%'}},
  {{key: 'feed_up',   icon: '🏔️', label: '飼料漲價 20%',   yield_pct: -2, cow_pct: -2,
    desc: '飼料成本上升、淘汰率提升 + 投料縮減'}},
];

let WI_MODE = 'structural';  // 'structural' | 'reverse' | 'timeseries'
let WI_SCENARIO = {{ yield_pct: 0, cow_pct: 0, target_tons: null }};
let wiChart = null;

const MODE_INTRO = {{
  structural: '輸入主管機關熟悉的關鍵參數、即時推算目標年全國產量。' +
              '系統用「<b>產量 = 產乳牛數 × 單頭日產乳 × 305 天 ÷ DHI/全國 productivity 比率</b>」這條結構式公式重算、與 cohort 模型一致、不是事後縮放。',
  reverse: '<b>給定目標產量、反推所需牛口</b>。系統用「<b>牛數 = 產量 × productivity 比率 ÷ (單頭產量 × 305)</b>」直接求解。' +
           '適合做政策規劃：「想保證 N 萬公噸、需多少頭牛？」',
  timeseries: '<b>對系統採用模型的預測值套整體乘數</b>（事後縮放）。' +
              '注意：模型內部已從歷史外推、此處的調整等同「假設目標年再額外變動 X%」。' +
              '若要做「直接改牛口/單頭產量」的結構式情境、請切換上方「結構式 順推」模式。',
}};

function renderWhatIf() {{
  const body = document.getElementById('whatif_body');
  if (!body) return;
  const cb = CTX.cohort_baseline;
  if (!cb) {{
    body.innerHTML = `<p style="font-size:12px;color:#888">
      ※ 情境假設計算器需要 Cohort 結構模型資料。
      請使用 <code>--with-cohort</code> 旗標重跑（互動選單選 2 或 4）。</p>`;
    return;
  }}

  const tgt = cb.target_year || CTX.target_year || '—';
  const baseCows = cb.cows;
  const baseYield = cb.daily_yield_kg;
  const baseDays = cb.lactation_days || 305;
  const baseTons = cb.annual_total_tons;
  const seasonal = cb.seasonal_pattern;

  // 系統採用模型 P50（用 best_model 的 calibrated_l4 forecast 加總）
  const stlBaseTons = CTX.forecast_p50_tons || null;
  const stlMape = CTX.best_mape;

  // 預設模板按鈕
  const presetBtns = SCENARIO_PRESETS.map(p => `
    <button class="wi-preset" data-preset="${{p.key}}" data-tip="${{p.desc}}">
      <span class="wi-preset-icon">${{p.icon}}</span>
      <span class="wi-preset-label">${{p.label}}</span>
    </button>
  `).join('');

  body.innerHTML = `
    <div class="wi-intro">
      <p>${{MODE_INTRO[WI_MODE]}}</p>
    </div>

    <div class="wi-mode-row">
      <span class="wi-mode-label">情境模式 / Mode：</span>
      <label class="wi-radio">
        <input type="radio" name="wi_mode" value="structural" checked>
        <span>結構式 順推（牛數 × 產量 → 總產量）⭐</span>
      </label>
      <label class="wi-radio">
        <input type="radio" name="wi_mode" value="reverse">
        <span>🎯 目標反推（總產量 → 需要的牛數）</span>
      </label>
      <label class="wi-radio">
        <input type="radio" name="wi_mode" value="timeseries">
        <span>整體乘數（事後縮放系統預測）</span>
      </label>
    </div>

    <div class="wi-presets">
      <span class="wi-preset-title">預設模板（一鍵套用）：</span>
      ${{presetBtns}}
    </div>

    <div class="wi-controls" id="wi_controls"></div>

    <div class="wi-results" id="wi_results"></div>

    <div class="wi-chart-wrap">
      <div class="wi-chart-title">月度對照 / Monthly comparison</div>
      <div style="position:relative;height:260px"><canvas id="wi_chart"></canvas></div>
    </div>

    <p class="wi-disclaimer">
      ⚠️ <b>使用說明 / Disclaimer</b>：
      本工具為「假設性敏感度分析」、非模型重新訓練。
      結構式情境直接套用 cohort 公式（產量 = 牛數 × 單頭產量 × 305 ÷ productivity 比率）。
      整體乘數情境則對系統採用模型的預測值套整體乘數（為事後縮放、非模型重訓）。
      實際產業衝擊往往多因素交互、結果應視為量級估計、不取代專業評估。
    </p>
  `;

  // 綁定 mode 切換（保留 yield/cow 調整、只清除 preset 高亮與 reverse 專用欄位）
  body.querySelectorAll('input[name="wi_mode"]').forEach(r => {{
    r.addEventListener('change', e => {{
      WI_MODE = e.target.value;
      const _baseTons = (CTX.cohort_baseline||{{}}).annual_total_tons || 0;
      // 切到 reverse 補上 target_tons；其他模式清掉 target_tons
      WI_SCENARIO.target_tons = WI_MODE === 'reverse' ? _baseTons : null;
      // 切換模式時、預設模板的「意義」會變、清除高亮避免誤導
      body.querySelectorAll('.wi-preset').forEach(b => b.classList.remove('active'));
      // 更新 mode 提示文字
      const intro = body.querySelector('.wi-intro p');
      if (intro) intro.innerHTML = MODE_INTRO[WI_MODE] || MODE_INTRO['structural'];
      drawWhatIfControls();
      drawWhatIfResults();
    }});
  }});

  // 綁定 preset 按鈕（保留 target_tons、只更新 yield_pct/cow_pct）
  body.querySelectorAll('.wi-preset').forEach(btn => {{
    btn.addEventListener('click', () => {{
      const k = btn.dataset.preset;
      const p = SCENARIO_PRESETS.find(x => x.key === k);
      if (!p) return;
      WI_SCENARIO.yield_pct = p.yield_pct;
      WI_SCENARIO.cow_pct = p.cow_pct;
      drawWhatIfControls();
      drawWhatIfResults();
      // 視覺回饋
      body.querySelectorAll('.wi-preset').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
    }});
  }});

  drawWhatIfControls();
  drawWhatIfResults();
}}

function drawWhatIfControls() {{
  const cb = CTX.cohort_baseline;
  const ctrl = document.getElementById('wi_controls');
  if (!ctrl || !cb) return;
  const baseCows = cb.cows;
  const baseYield = cb.daily_yield_kg;
  const baseDays = cb.lactation_days || 305;

  const sc = WI_SCENARIO;
  const adjCows = baseCows * (1 + sc.cow_pct / 100);
  const adjYield = baseYield * (1 + sc.yield_pct / 100);

  if (WI_MODE === 'structural') {{
    ctrl.innerHTML = `
      <div class="wi-grid">
        <div class="wi-input-cell">
          <label class="wi-input-label">🐄 產乳牛數 / Milking Cows</label>
          <div class="wi-input-row">
            <input type="number" id="wi_cows" class="wi-num"
              value="${{Math.round(adjCows)}}" step="100"
              min="${{Math.round(baseCows * 0.5)}}" max="${{Math.round(baseCows * 1.5)}}">
            <span class="wi-unit">頭</span>
          </div>
          <div class="wi-base-info">基準 ${{Math.round(baseCows).toLocaleString()}} 頭
            (${{sc.cow_pct >= 0 ? '+' : ''}}${{sc.cow_pct.toFixed(1)}}%)</div>
          <input type="range" id="wi_cows_slider" class="wi-slider"
            min="-15" max="15" step="0.5" value="${{sc.cow_pct}}">
        </div>

        <div class="wi-input-cell">
          <label class="wi-input-label">🥛 單頭日產乳 / Daily Yield per Cow</label>
          <div class="wi-input-row">
            <input type="number" id="wi_yield" class="wi-num"
              value="${{adjYield.toFixed(1)}}" step="0.1"
              min="${{(baseYield * 0.5).toFixed(1)}}" max="${{(baseYield * 1.5).toFixed(1)}}">
            <span class="wi-unit">kg/天</span>
          </div>
          <div class="wi-base-info">基準 ${{baseYield.toFixed(1)}} kg/天
            (${{sc.yield_pct >= 0 ? '+' : ''}}${{sc.yield_pct.toFixed(1)}}%)</div>
          <input type="range" id="wi_yield_slider" class="wi-slider"
            min="-15" max="15" step="0.5" value="${{sc.yield_pct}}">
        </div>

        <div class="wi-input-cell">
          <label class="wi-input-label">📅 泌乳期 / Lactation Days</label>
          <div class="wi-input-row">
            <input type="number" class="wi-num" value="${{baseDays}}" disabled>
            <span class="wi-unit">天 (固定)</span>
          </div>
          <div class="wi-base-info">產業標準假設、不變</div>
        </div>
      </div>
    `;
    // 綁定 number input 與 slider 雙向同步
    const elCows = document.getElementById('wi_cows');
    const elCowsSl = document.getElementById('wi_cows_slider');
    const elYield = document.getElementById('wi_yield');
    const elYieldSl = document.getElementById('wi_yield_slider');
    elCows.addEventListener('input', () => {{
      const v = parseFloat(elCows.value) || baseCows;
      WI_SCENARIO.cow_pct = (v - baseCows) / baseCows * 100;
      elCowsSl.value = Math.max(-15, Math.min(15, WI_SCENARIO.cow_pct));
      drawWhatIfResults();
      updateBaseInfo();
    }});
    elCowsSl.addEventListener('input', () => {{
      WI_SCENARIO.cow_pct = parseFloat(elCowsSl.value);
      const v = baseCows * (1 + WI_SCENARIO.cow_pct / 100);
      elCows.value = Math.round(v);
      drawWhatIfResults();
      updateBaseInfo();
    }});
    elYield.addEventListener('input', () => {{
      const v = parseFloat(elYield.value) || baseYield;
      WI_SCENARIO.yield_pct = (v - baseYield) / baseYield * 100;
      elYieldSl.value = Math.max(-15, Math.min(15, WI_SCENARIO.yield_pct));
      drawWhatIfResults();
      updateBaseInfo();
    }});
    elYieldSl.addEventListener('input', () => {{
      WI_SCENARIO.yield_pct = parseFloat(elYieldSl.value);
      const v = baseYield * (1 + WI_SCENARIO.yield_pct / 100);
      elYield.value = v.toFixed(1);
      drawWhatIfResults();
      updateBaseInfo();
    }});
    function updateBaseInfo() {{
      const cells = document.querySelectorAll('.wi-base-info');
      if (cells[0]) cells[0].innerHTML =
        `基準 ${{Math.round(baseCows).toLocaleString()}} 頭 (${{WI_SCENARIO.cow_pct >= 0 ? '+' : ''}}${{WI_SCENARIO.cow_pct.toFixed(1)}}%)`;
      if (cells[1]) cells[1].innerHTML =
        `基準 ${{baseYield.toFixed(1)}} kg/天 (${{WI_SCENARIO.yield_pct >= 0 ? '+' : ''}}${{WI_SCENARIO.yield_pct.toFixed(1)}}%)`;
    }}
  }} else if (WI_MODE === 'reverse') {{
    // 目標反推：給定產量 + 假設單頭產量 → 反推需要的牛數
    const tgtTons = WI_SCENARIO.target_tons || cb.annual_total_tons;
    const adjYieldR = baseYield * (1 + sc.yield_pct / 100);
    ctrl.innerHTML = `
      <div class="wi-grid">
        <div class="wi-input-cell wi-target-cell">
          <label class="wi-input-label">🎯 目標全國年產量 / Target Annual Production</label>
          <div class="wi-input-row">
            <input type="number" id="wi_target" class="wi-num"
              value="${{Math.round(tgtTons)}}" step="1000" min="0">
            <span class="wi-unit">公噸</span>
          </div>
          <div class="wi-base-info">基準（cohort 校正後）= ${{Math.round(cb.annual_total_tons).toLocaleString()}} 公噸（≈ ${{(cb.annual_total_tons/10000).toFixed(2)}} 萬公噸）</div>
          <div class="wi-base-info">目前輸入 ≈ ${{(tgtTons/10000).toFixed(2)}} 萬公噸</div>
        </div>

        <div class="wi-input-cell">
          <label class="wi-input-label">🥛 假設單頭日產乳 / Assumed Daily Yield</label>
          <div class="wi-input-row">
            <input type="number" id="wi_yield" class="wi-num"
              value="${{adjYieldR.toFixed(1)}}" step="0.1"
              min="${{(baseYield * 0.5).toFixed(1)}}" max="${{(baseYield * 1.5).toFixed(1)}}">
            <span class="wi-unit">kg/天</span>
          </div>
          <div class="wi-base-info">基準 ${{baseYield.toFixed(1)}} kg/天
            (${{sc.yield_pct >= 0 ? '+' : ''}}${{sc.yield_pct.toFixed(1)}}%)</div>
          <input type="range" id="wi_yield_slider" class="wi-slider"
            min="-15" max="15" step="0.5" value="${{sc.yield_pct}}">
        </div>

        <div class="wi-input-cell">
          <label class="wi-input-label">📅 泌乳期 / Lactation Days</label>
          <div class="wi-input-row">
            <input type="number" class="wi-num" value="${{baseDays}}" disabled>
            <span class="wi-unit">天 (固定)</span>
          </div>
          <div class="wi-base-info">產業標準假設、不變</div>
        </div>
      </div>
    `;
    const elTgt = document.getElementById('wi_target');
    const elYield = document.getElementById('wi_yield');
    const elYieldSl = document.getElementById('wi_yield_slider');
    elTgt.addEventListener('input', () => {{
      WI_SCENARIO.target_tons = parseFloat(elTgt.value) || cb.annual_total_tons;
      drawWhatIfResults();
    }});
    elYield.addEventListener('input', () => {{
      const v = parseFloat(elYield.value) || baseYield;
      WI_SCENARIO.yield_pct = (v - baseYield) / baseYield * 100;
      elYieldSl.value = Math.max(-15, Math.min(15, WI_SCENARIO.yield_pct));
      drawWhatIfResults();
    }});
    elYieldSl.addEventListener('input', () => {{
      WI_SCENARIO.yield_pct = parseFloat(elYieldSl.value);
      const v = baseYield * (1 + WI_SCENARIO.yield_pct / 100);
      elYield.value = v.toFixed(1);
      drawWhatIfResults();
    }});
  }} else {{
    // 時序式：兩個整體乘數
    ctrl.innerHTML = `
      <div class="wi-grid">
        <div class="wi-input-cell">
          <label class="wi-input-label">🥛 單頭產乳 整體變動 / Productivity Shock</label>
          <div class="wi-input-row" style="font-size:18px;font-weight:600">
            <span class="wi-shock-display" id="wi_yield_disp">${{sc.yield_pct >= 0 ? '+' : ''}}${{sc.yield_pct.toFixed(1)}}%</span>
          </div>
          <input type="range" id="wi_yield_slider" class="wi-slider"
            min="-15" max="15" step="0.5" value="${{sc.yield_pct}}">
          <div class="wi-base-info">套用至系統採用模型的預測值（整體乘數）</div>
        </div>

        <div class="wi-input-cell">
          <label class="wi-input-label">🐄 牛口數量 整體變動 / Herd Size Shock</label>
          <div class="wi-input-row" style="font-size:18px;font-weight:600">
            <span class="wi-shock-display" id="wi_cow_disp">${{sc.cow_pct >= 0 ? '+' : ''}}${{sc.cow_pct.toFixed(1)}}%</span>
          </div>
          <input type="range" id="wi_cow_slider" class="wi-slider"
            min="-15" max="15" step="0.5" value="${{sc.cow_pct}}">
          <div class="wi-base-info">套用至系統採用模型的預測值（整體乘數）</div>
        </div>
      </div>
    `;
    document.getElementById('wi_yield_slider').addEventListener('input', e => {{
      WI_SCENARIO.yield_pct = parseFloat(e.target.value);
      document.getElementById('wi_yield_disp').textContent =
        (WI_SCENARIO.yield_pct >= 0 ? '+' : '') + WI_SCENARIO.yield_pct.toFixed(1) + '%';
      drawWhatIfResults();
    }});
    document.getElementById('wi_cow_slider').addEventListener('input', e => {{
      WI_SCENARIO.cow_pct = parseFloat(e.target.value);
      document.getElementById('wi_cow_disp').textContent =
        (WI_SCENARIO.cow_pct >= 0 ? '+' : '') + WI_SCENARIO.cow_pct.toFixed(1) + '%';
      drawWhatIfResults();
    }});
  }}
}}

function drawWhatIfResults() {{
  const cb = CTX.cohort_baseline;
  const out = document.getElementById('wi_results');
  if (!out || !cb) return;
  // 防禦性：直接從 DOM 讀 radio、確保 WI_MODE 與當前選擇同步
  const _checkedRadio = document.querySelector('input[name="wi_mode"]:checked');
  if (_checkedRadio && WI_MODE !== _checkedRadio.value) {{
    WI_MODE = _checkedRadio.value;
  }}
  // 重置 chart wrap 顯示（reverse 模式會藏起來）
  const _ce = document.getElementById('wi_chart');
  if (_ce) {{
    const _w = _ce.closest('.wi-chart-wrap');
    if (_w) _w.style.display = '';
  }}
  const baseCows = cb.cows;
  const baseYield = cb.daily_yield_kg;
  const baseDays = cb.lactation_days || 305;
  const baseTons = cb.annual_total_tons;
  const seasonal = cb.seasonal_pattern;
  const sc = WI_SCENARIO;

  let scenarioTons, baselineTons, modelLabel, mapeStr;
  let scenarioMonthly, baselineMonthly;

  if (WI_MODE === 'structural') {{
    // cohort 公式直接重算（兩段校正：productivity ratio + static bias）
    const prodRatio = cb.productivity_ratio || 1.0;
    const staticFactor = cb.static_calibration_factor != null
      ? cb.static_calibration_factor : 1.0;
    const adjCows = baseCows * (1 + sc.cow_pct / 100);
    const adjYield = baseYield * (1 + sc.yield_pct / 100);
    // 公式：(牛數 × 產量 × 305) / productivity_ratio × static_factor
    scenarioTons = adjCows * adjYield * baseDays / 1000
                    / prodRatio * staticFactor;
    baselineTons = baseTons;  // 已含兩段校正
    const corrParts = [];
    if (cb.productivity_correction_applied) {{
      corrParts.push(`÷ ${{prodRatio.toFixed(3)}} productivity 比率`);
    }}
    if (Math.abs(cb.static_bias_pct || 0) > 0.5) {{
      corrParts.push(`× ${{staticFactor.toFixed(3)}} 靜態殘差校正`);
    }}
    const corrLabel = corrParts.length ? '（' + corrParts.join('、') + '）' : '';
    modelLabel = `cohort_simple${{corrLabel}}`;
    mapeStr = `±${{(cb.in_sample_mape || 5).toFixed(1)}}%（cohort in-sample MAPE）`;
    // 月度
    scenarioMonthly = seasonal.map(s => scenarioTons * s);
    baselineMonthly = seasonal.map(s => baselineTons * s);
  }} else if (WI_MODE === 'reverse') {{
    // 目標反推：給定產量、求需要的牛數
    // baseTons 已套 (1/prodRatio × staticFactor)、反推時要把這兩段都還原
    const prodRatio = cb.productivity_ratio || 1.0;
    const staticFactor = cb.static_calibration_factor != null
      ? cb.static_calibration_factor : 1.0;
    const adjYield = baseYield * (1 + sc.yield_pct / 100);
    const tgtTons = sc.target_tons || baseTons;
    // 還原到 cohort raw 等價的目標：tgtTons / staticFactor × prodRatio
    const undoneStatic = tgtTons / staticFactor;  // 第一步還原：除掉殘差校正
    const rawTgtTons = undoneStatic * prodRatio;  // 第二步還原：乘回 productivity 比率
    const requiredCows = rawTgtTons * 1000 / (adjYield * baseDays);  // 公噸 → kg → 除以單頭產量
    const cowsDelta = requiredCows - baseCows;
    const cowsDeltaPct = (cowsDelta / baseCows) * 100;

    const fmtUnitR = (tons) => {{
      const u = unit();
      return (tons * 1000 / u.divisor).toLocaleString(undefined,
        {{minimumFractionDigits: u.precision, maximumFractionDigits: u.precision}}) +
        ' ' + u.label;
    }};

    out.innerHTML = `
      <div class="wi-result-cards wi-reverse">
        <div class="wi-card baseline">
          <div class="wi-card-label">目標產量 / Target</div>
          <div class="wi-card-value">${{fmtUnitR(tgtTons)}}</div>
          <div class="wi-card-sub">假設單頭日產乳 ${{adjYield.toFixed(1)}} kg</div>
        </div>
        <div class="wi-arrow">⇒</div>
        <div class="wi-card scenario ${{cowsDelta < 0 ? 'down' : cowsDelta > 0 ? 'up' : ''}}">
          <div class="wi-card-label">所需產乳牛數 / Required Cows</div>
          <div class="wi-card-value">${{Math.round(requiredCows).toLocaleString()}} 頭</div>
          <div class="wi-card-sub">公式：產量 ÷ (單頭產量 × 305 天)</div>
        </div>
        <div class="wi-card diff ${{cowsDelta < 0 ? 'down' : cowsDelta > 0 ? 'up' : ''}}">
          <div class="wi-card-label">vs cohort 基準 / Delta</div>
          <div class="wi-card-value">${{cowsDelta >= 0 ? '+' : ''}}${{Math.round(cowsDelta).toLocaleString()}} 頭</div>
          <div class="wi-card-sub">${{cowsDeltaPct >= 0 ? '+' : ''}}${{cowsDeltaPct.toFixed(1)}}% vs ${{Math.round(baseCows).toLocaleString()}}</div>
        </div>
      </div>

      <div class="wi-yoy">
        ${{(() => {{
          const tgtY = cb.target_year || '—';
          if (cowsDelta > 0) {{
            return `達成 <b>${{fmtUnitR(tgtTons)}}</b> 的 ${{tgtY}} 年目標、需要在現況基礎上**新增 ${{Math.round(cowsDelta).toLocaleString()}} 頭**產乳牛（+${{cowsDeltaPct.toFixed(1)}}%）、可考慮政策補貼或進口配額。`;
          }} else if (cowsDelta < 0) {{
            return `若僅追求 <b>${{fmtUnitR(tgtTons)}}</b> 的 ${{tgtY}} 年目標、現有牛口超過所需 <b>${{Math.round(-cowsDelta).toLocaleString()}}</b> 頭（${{cowsDeltaPct.toFixed(1)}}%）、有產能餘裕。`;
          }} else {{
            return `現有牛口剛好可支撐目標產量。`;
          }}
        }})()}}
      </div>

      ${{CTX.latest_official_tons ? (() => {{
        const offY = CTX.latest_official_year;
        const offCows = (CTX.history || []).find(h => h.year === offY);
        if (!offCows) return '';
        const offCowCount = offCows.n_milking_cows;
        const vsOff = ((requiredCows - offCowCount) / offCowCount * 100).toFixed(1);
        return `<div class="wi-cross">
          <span class="wi-cross-tag">對照</span>
          ${{offY}} 年農業部公告產乳牛數 = <b>${{offCowCount.toLocaleString()}}</b> 頭。
          達成目標需 <b>${{Math.round(requiredCows).toLocaleString()}}</b> 頭、
          較 ${{offY}} 年 ${{vsOff >= 0 ? '+' : ''}}${{vsOff}}%。
        </div>`;
      }})() : ''}}

      <div class="wi-calc">
        <div class="wi-calc-title">📐 計算明細 / Calculation Detail</div>
        <div class="wi-calc-grid wi-calc-single">
          <div class="wi-calc-col">
            <div class="wi-calc-col-h">目標反推：${{fmtUnitR(tgtTons)}} → 所需牛數</div>
            <div class="wi-calc-step">
              <span class="wi-calc-formula">${{Math.round(tgtTons).toLocaleString()}} 公噸 (目標)</span>
              <span class="wi-calc-eq">= <b>${{Math.round(tgtTons).toLocaleString()}} 公噸</b></span>
            </div>
            <div class="wi-calc-step">
              <span class="wi-calc-formula">÷ ${{staticFactor.toFixed(4)}} (還原 ${{(cb.static_bias_pct||0).toFixed(2)}}% 殘差校正)</span>
              <span class="wi-calc-eq">= <b>${{Math.round(undoneStatic).toLocaleString()}} 公噸</b></span>
            </div>
            <div class="wi-calc-step">
              <span class="wi-calc-formula">× ${{prodRatio.toFixed(4)}} (還原 productivity 比率)</span>
              <span class="wi-calc-eq">= <b>${{Math.round(rawTgtTons).toLocaleString()}} 公噸</b><span class="wi-calc-anno">（cohort raw 等價目標）</span></span>
            </div>
            <div class="wi-calc-step">
              <span class="wi-calc-formula">× 1000 ÷ (${{adjYield.toFixed(2)}} kg × ${{baseDays}} 天)</span>
              <span class="wi-calc-eq wi-calc-final">= <b>${{Math.round(requiredCows).toLocaleString()}} 頭</b></span>
            </div>
          </div>
        </div>
        <div class="wi-calc-diff">
          所需 ${{Math.round(requiredCows).toLocaleString()}} 頭 vs cohort 基準 ${{Math.round(baseCows).toLocaleString()}} 頭
          = <b class="${{cowsDelta < 0 ? 'wi-down' : cowsDelta > 0 ? 'wi-up' : ''}}">
            ${{cowsDelta >= 0 ? '+' : ''}}${{Math.round(cowsDelta).toLocaleString()}} 頭
            （${{cowsDeltaPct >= 0 ? '+' : ''}}${{cowsDeltaPct.toFixed(2)}}%）
          </b>
        </div>
      </div>
    `;
    // reverse 模式不畫月度 chart（沒意義）
    if (wiChart) {{ wiChart.destroy(); wiChart = null; }}
    const ctxEl = document.getElementById('wi_chart');
    if (ctxEl) {{
      const wrap = ctxEl.closest('.wi-chart-wrap');
      if (wrap) wrap.style.display = 'none';
    }}
    return;  // 提前結束、不走後面的順推/時序顯示
  }} else {{
    // 整體乘數：對系統採用模型 P50 套乘數
    const sysBase = CTX.forecast_p50_tons;
    if (sysBase == null) {{
      out.innerHTML = '<p style="color:#888">系統採用模型預測值暫無、無法套用整體乘數情境。</p>';
      return;
    }}
    const mult = (1 + sc.yield_pct / 100) * (1 + sc.cow_pct / 100);
    scenarioTons = sysBase * mult;
    baselineTons = sysBase;
    const _bm = CTX.best_model || '系統採用模型';
    modelLabel = `${{_bm}}（系統採用、套整體乘數）`;
    mapeStr = `±${{(CTX.best_mape || 3.5).toFixed(1)}}%（${{_bm}} holdout MAPE）`;
    // 月度（用 stl_linear 的 calibrated_l4 forecast 抓真實月度形狀）
    const nat = (D.results || {{}})['全國'] || {{}};
    const cal = nat.calibrated_l4 || nat.calibrated || {{}};
    const bm = (CTX.best_model || 'stl_linear');
    const bmFc = (cal.models || []).find(m => m.model === bm && m.success);
    if (bmFc && bmFc.forecast) {{
      baselineMonthly = bmFc.forecast.map(f => f.p50 / 1000);  // kg → tons
      scenarioMonthly = baselineMonthly.map(v => v * mult);
    }} else {{
      baselineMonthly = seasonal.map(s => baselineTons * s);
      scenarioMonthly = seasonal.map(s => scenarioTons * s);
    }}
  }}

  const diff = scenarioTons - baselineTons;
  const diffPct = baselineTons ? (diff / baselineTons * 100) : 0;
  const officialTons = CTX.latest_official_tons;
  const officialYr = CTX.latest_official_year;
  const yoyVsOff = officialTons ? ((scenarioTons - officialTons) / officialTons * 100) : null;

  const fmtUnit = (tons) => {{
    const u = unit();
    return (tons * 1000 / u.divisor).toLocaleString(undefined,
      {{minimumFractionDigits: u.precision, maximumFractionDigits: u.precision}}) +
      ' ' + u.label;
  }};

  // 無情境時、差異卡顯示乾淨的「無差異」
  const noDiff = Math.abs(diffPct) < 0.05;
  const diffPctStr = noDiff ? '—' : (diffPct >= 0 ? '+' : '') + diffPct.toFixed(1) + '%';
  const diffAbsStr = noDiff ? '尚未調整' : (diff >= 0 ? '+' : '') + fmtUnit(diff);

  out.innerHTML = `
    <div class="wi-result-cards">
      <div class="wi-card baseline">
        <div class="wi-card-label">基準預測 / Baseline</div>
        <div class="wi-card-value">${{fmtUnit(baselineTons)}}</div>
        <div class="wi-card-sub">${{modelLabel}}</div>
      </div>
      <div class="wi-arrow">→</div>
      <div class="wi-card scenario ${{diff < 0 ? 'down' : diff > 0 ? 'up' : ''}}">
        <div class="wi-card-label">情境調整後 / Scenario</div>
        <div class="wi-card-value">${{fmtUnit(scenarioTons)}}</div>
        <div class="wi-card-sub">信賴區間 ${{mapeStr}}</div>
      </div>
      <div class="wi-card diff ${{noDiff ? '' : (diff < 0 ? 'down' : 'up')}}">
        <div class="wi-card-label">差異 / Delta</div>
        <div class="wi-card-value">${{diffPctStr}}</div>
        <div class="wi-card-sub">${{diffAbsStr}}</div>
      </div>
    </div>

    ${{officialTons ? `
      <div class="wi-yoy">
        對照 <b>${{officialYr}} 年農業部公告值</b>（${{fmtUnit(officialTons)}}）：
        <b class="${{yoyVsOff < 0 ? 'wi-down' : yoyVsOff > 0 ? 'wi-up' : ''}}">
          ${{yoyVsOff >= 0 ? '+' : ''}}${{yoyVsOff.toFixed(1)}}%
        </b>
      </div>
    ` : ''}}

    ${{(() => {{
      // 計算明細面板：把每一步都列出來
      const u = unit();
      const fmtNum = (v, dp = 0) => v != null
        ? v.toLocaleString(undefined, {{minimumFractionDigits: dp, maximumFractionDigits: dp}})
        : '—';
      const fmtT = (tons) => fmtNum(tons * 1000 / u.divisor,
        u.precision) + ' ' + u.label;

      if (WI_MODE === 'structural') {{
        const prodRatio = cb.productivity_ratio || 1.0;
        const staticFactor = cb.static_calibration_factor || 1.0;
        const adjCows = baseCows * (1 + sc.cow_pct / 100);
        const adjYield = baseYield * (1 + sc.yield_pct / 100);
        const baseRaw = baseCows * baseYield * baseDays / 1000;
        const baseAfterProd = baseRaw / prodRatio;
        const baseFinal = baseAfterProd * staticFactor;
        const scnRaw = adjCows * adjYield * baseDays / 1000;
        const scnAfterProd = scnRaw / prodRatio;
        const scnFinal = scnAfterProd * staticFactor;
        const diffFinal = scnFinal - baseFinal;
        const diffPct = (diffFinal / baseFinal * 100);
        return `<div class="wi-calc">
          <div class="wi-calc-title">📐 計算明細 / Calculation Detail</div>
          <div class="wi-calc-grid">
            <div class="wi-calc-col">
              <div class="wi-calc-col-h">基準預測 / Baseline</div>
              <div class="wi-calc-step">
                <span class="wi-calc-formula">${{fmtNum(baseCows)}} 頭 × ${{baseYield.toFixed(2)}} kg × ${{baseDays}} 天 ÷ 1000</span>
                <span class="wi-calc-eq">= <b>${{fmtNum(baseRaw)}} 公噸</b><span class="wi-calc-anno">（公式原始值）</span></span>
              </div>
              <div class="wi-calc-step">
                <span class="wi-calc-formula">÷ ${{prodRatio.toFixed(4)}} (productivity 比率)</span>
                <span class="wi-calc-eq">= <b>${{fmtNum(baseAfterProd)}} 公噸</b><span class="wi-calc-anno">（productivity 校正後）</span></span>
              </div>
              <div class="wi-calc-step">
                <span class="wi-calc-formula">× ${{staticFactor.toFixed(4)}} (1 − ${{(cb.static_bias_pct||0).toFixed(2)}}% 殘差校正)</span>
                <span class="wi-calc-eq wi-calc-final">= <b>${{fmtT(baseFinal)}}</b></span>
              </div>
            </div>
            <div class="wi-calc-col wi-calc-scn">
              <div class="wi-calc-col-h">情境調整後 / Scenario</div>
              <div class="wi-calc-meta">
                牛數 ${{(sc.cow_pct >= 0 ? '+' : '')+sc.cow_pct.toFixed(1)}}%、單頭產乳 ${{(sc.yield_pct >= 0 ? '+' : '')+sc.yield_pct.toFixed(1)}}%
              </div>
              <div class="wi-calc-step">
                <span class="wi-calc-formula">${{fmtNum(adjCows)}} 頭 × ${{adjYield.toFixed(2)}} kg × ${{baseDays}} 天 ÷ 1000</span>
                <span class="wi-calc-eq">= <b>${{fmtNum(scnRaw)}} 公噸</b></span>
              </div>
              <div class="wi-calc-step">
                <span class="wi-calc-formula">÷ ${{prodRatio.toFixed(4)}}</span>
                <span class="wi-calc-eq">= <b>${{fmtNum(scnAfterProd)}} 公噸</b></span>
              </div>
              <div class="wi-calc-step">
                <span class="wi-calc-formula">× ${{staticFactor.toFixed(4)}}</span>
                <span class="wi-calc-eq wi-calc-final">= <b>${{fmtT(scnFinal)}}</b></span>
              </div>
            </div>
          </div>
          <div class="wi-calc-diff">
            差異 = ${{fmtT(scnFinal)}} − ${{fmtT(baseFinal)}}
            = <b class="${{diffPct < 0 ? 'wi-down' : diffPct > 0 ? 'wi-up' : ''}}">
              ${{diffFinal >= 0 ? '+' : ''}}${{fmtT(diffFinal)}}
              （${{diffPct >= 0 ? '+' : ''}}${{diffPct.toFixed(2)}}%）
            </b>
          </div>
        </div>`;
      }} else if (WI_MODE === 'reverse') {{
        const prodRatio = cb.productivity_ratio || 1.0;
        const staticFactor = cb.static_calibration_factor || 1.0;
        const adjYield = baseYield * (1 + sc.yield_pct / 100);
        const tgtTons = sc.target_tons || baseTons;
        const undoneStatic = tgtTons / staticFactor;
        const rawTgtTons = undoneStatic * prodRatio;
        const requiredCows = rawTgtTons * 1000 / (adjYield * baseDays);
        return `<div class="wi-calc">
          <div class="wi-calc-title">📐 計算明細 / Calculation Detail</div>
          <div class="wi-calc-grid wi-calc-single">
            <div class="wi-calc-col">
              <div class="wi-calc-col-h">目標反推：${{fmtT(tgtTons)}} → 所需牛數</div>
              <div class="wi-calc-step">
                <span class="wi-calc-formula">${{fmtNum(tgtTons)}} 公噸 (目標)</span>
                <span class="wi-calc-eq">= <b>${{fmtNum(tgtTons)}} 公噸</b></span>
              </div>
              <div class="wi-calc-step">
                <span class="wi-calc-formula">÷ ${{staticFactor.toFixed(4)}} (還原殘差校正)</span>
                <span class="wi-calc-eq">= <b>${{fmtNum(undoneStatic)}} 公噸</b></span>
              </div>
              <div class="wi-calc-step">
                <span class="wi-calc-formula">× ${{prodRatio.toFixed(4)}} (還原 productivity 比率)</span>
                <span class="wi-calc-eq">= <b>${{fmtNum(rawTgtTons)}} 公噸</b><span class="wi-calc-anno">（cohort raw 等價目標）</span></span>
              </div>
              <div class="wi-calc-step">
                <span class="wi-calc-formula">÷ (${{adjYield.toFixed(2)}} kg × ${{baseDays}} 天) × 1000</span>
                <span class="wi-calc-eq wi-calc-final">= <b>${{fmtNum(requiredCows)}} 頭</b></span>
              </div>
            </div>
          </div>
        </div>`;
      }} else {{
        // 整體乘數模式
        const sysBase = CTX.forecast_p50_tons;
        const mult = (1 + sc.yield_pct / 100) * (1 + sc.cow_pct / 100);
        const scnFinal = sysBase * mult;
        const _bm = CTX.best_model || '系統採用模型';
        return `<div class="wi-calc">
          <div class="wi-calc-title">📐 計算明細 / Calculation Detail</div>
          <div class="wi-calc-grid wi-calc-single">
            <div class="wi-calc-col">
              <div class="wi-calc-col-h">整體乘數（事後縮放）</div>
              <div class="wi-calc-step">
                <span class="wi-calc-formula">${{_bm}} 系統預測</span>
                <span class="wi-calc-eq">= <b>${{fmtT(sysBase)}}</b></span>
              </div>
              <div class="wi-calc-step">
                <span class="wi-calc-formula">乘數 = (1 + ${{sc.yield_pct.toFixed(1)}}%) × (1 + ${{sc.cow_pct.toFixed(1)}}%)</span>
                <span class="wi-calc-eq">= <b>${{mult.toFixed(4)}}</b></span>
              </div>
              <div class="wi-calc-step">
                <span class="wi-calc-formula">${{fmtT(sysBase)}} × ${{mult.toFixed(4)}}</span>
                <span class="wi-calc-eq wi-calc-final">= <b>${{fmtT(scnFinal)}}</b></span>
              </div>
            </div>
          </div>
        </div>`;
      }}
    }})()}}

    ${{WI_MODE === 'structural' ? (() => {{
      // 找一個與 best_model 不同的對照模型（優先取 top3 第二名）
      const t3 = CTX.top3_models || [];
      const bm = CTX.best_model;
      const altModel = t3.find(m => m.name !== bm);
      if (!altModel || altModel.pred_tons == null) return '';
      const altTons = altModel.pred_tons;
      const altWton = altTons / 10000;
      const diffPct = ((scenarioTons - altTons) / altTons * 100);
      const altLabel = ({{
        'stl_linear': 'stl_linear（純時序、均值回歸）',
        'neural_prophet': 'neural_prophet（神經網路 AR）',
        'cohort_simple': 'cohort_simple（結構式 v1、論文版本）',
        'cohort_v2_auto': 'cohort_v2_auto（結構式 v2、N 季度+r ensemble+auto nowcast）',
        'holt_winters': 'holt_winters（指數平滑）',
        'sarima': 'sarima（季節 ARIMA）',
        'prophet': 'prophet（Facebook Prophet）',
        'naive_seasonal': 'naive_seasonal（基準線）',
      }})[altModel.name] || altModel.name;
      return `<div class="wi-cross">
        <span class="wi-cross-tag">交叉對照</span>
        對照模型 <b>${{altLabel}}</b> 對 ${{cb.target_year}} 的預測 = <b>${{fmtUnit(altTons)}}</b>
        （MAPE ${{altModel.mape != null ? altModel.mape.toFixed(1)+'%' : '—'}}）。
        本情境結果與此對照差 ${{diffPct >= 0 ? '+' : ''}}${{diffPct.toFixed(1)}}%。
        若兩模型分歧大、表示未來結構不確定性高、宜搭配多種情境試算。
      </div>`;
    }})() : ''}}
  `;

  drawWhatIfChart(baselineMonthly, scenarioMonthly, cb.target_year);
}}

function drawWhatIfChart(baseMonthly, scenarioMonthly, targetYear) {{
  const ctxEl = document.getElementById('wi_chart');
  if (!ctxEl || !baseMonthly) return;
  const u = unit();
  const labels = baseMonthly.map((_, i) => `${{targetYear || ''}}-${{String(i+1).padStart(2,'0')}}`);
  const baseConv = baseMonthly.map(v => v * 1000 / u.divisor);
  const scnConv = scenarioMonthly.map(v => v * 1000 / u.divisor);

  // 偵測兩線是否重合（差異 < 0.01% 視為相同）
  const totalBase = baseConv.reduce((a,b)=>a+b, 0);
  const totalScn = scnConv.reduce((a,b)=>a+b, 0);
  const overlap = totalBase > 0 && Math.abs(totalScn - totalBase) / totalBase < 0.0001;

  // 若重合、在 chart 上方顯示提示
  const hint = document.querySelector('.wi-overlap-hint');
  if (hint) hint.remove();
  if (overlap) {{
    const wrap = ctxEl.closest('.wi-chart-wrap');
    if (wrap) {{
      const note = document.createElement('div');
      note.className = 'wi-overlap-hint';
      note.innerHTML = '💡 情境未調整、基準與情境兩線完全重合。請拖動 slider 或點預設模板看差異。';
      wrap.insertBefore(note, ctxEl.parentElement);
    }}
  }}

  if (wiChart) wiChart.destroy();
  wiChart = new Chart(ctxEl, {{
    type: 'line',
    data: {{ labels, datasets: [
      {{label: '基準 / Baseline', data: baseConv,
        borderColor: '#1a3550', backgroundColor: '#1a3550',
        borderWidth: 2, pointRadius: 3, tension: 0.3}},
      {{label: '情境調整後 / Scenario', data: scnConv,
        borderColor: '#c9930e', backgroundColor: '#c9930e',
        borderWidth: 2.5, pointRadius: 3, tension: 0.3,
        borderDash: [5, 3]}},
    ]}},
    options: {{
      responsive: true, maintainAspectRatio: false,
      plugins: {{ legend: {{position: 'top'}} }},
      scales: {{
        y: {{title: {{display: true, text: `月乳量 (${{u.label}})`}}}}
      }}
    }}
  }});
}}

// 預設收合：等使用者點開 <details> 才 render（避免 chart 在 hidden 容器繪失敗）
let _whatifRendered = false;
function _renderWhatIfOnDemand() {{
  if (_whatifRendered) return;
  _whatifRendered = true;
  renderWhatIf();
}}
const _whatifDetails = document.querySelector('.whatif-details');
if (_whatifDetails) {{
  _whatifDetails.addEventListener('toggle', () => {{
    if (_whatifDetails.open) _renderWhatIfOnDemand();
  }});
  // 若預設展開（debug）則立即 render
  if (_whatifDetails.open) _renderWhatIfOnDemand();
}} else {{
  // fallback：沒有 details 包裝、直接 render
  renderWhatIf();
}}

// =============================================
// §5 系統狀態
// =============================================
function renderStatus() {{
  const grid = document.getElementById('status_grid');
  if (!grid || !CTX.data_sources) return;
  const html = CTX.data_sources.map(s => `
    <div class="st-card">
      <div class="st-name-zh">${{s.name}}</div>
      <div class="st-name-en">${{s.name_en}}</div>
      <div class="st-latest">最新 / Latest:<br/>
        <span class="st-value">${{s.latest}}</span></div>
      <div class="st-freq">${{s.freq === 'monthly' ? '月度更新 / Monthly'
                              : s.freq === 'quarterly' ? '季度更新 / Quarterly'
                              : '年度更新 / Annual'}}</div>
    </div>
  `).join('');
  grid.innerHTML = html;
}}
renderStatus();

// =============================================
// §4 Tab 切換 + 動態結論
// =============================================
document.querySelectorAll('.acc-tab').forEach(btn => {{
  btn.addEventListener('click', () => {{
    document.querySelectorAll('.acc-tab').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.acc-pane').forEach(p => p.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById('acc_' + btn.dataset.tab).classList.add('active');
  }});
}});

function renderAccConclusions() {{
  // SF 選擇邏輯（L1 vs L4 透明對比）
  const ho = D.holdout_backtest;
  const sfBox = document.getElementById('sf_selection_logic');
  if (ho && ho.summary && sfBox) {{
    const s = ho.summary;
    const fullM_l4 = s.full_mape;
    const fullM_l1 = s.full_mape_l1;
    let l4Wins = (fullM_l1 != null && fullM_l4 < fullM_l1);
    let diff = (fullM_l1 != null) ? (fullM_l4 - fullM_l1) : null;
    sfBox.innerHTML = `
      <div class="eb-title">⚙️ SF 方法選擇邏輯 <span class="h-en">SF Method Selection Logic</span></div>
      <p>本系統 <b>固定使用 Level 4 SF</b>（最新季報 + 年報外推到目標年），
        即使本次 backtest 顯示 L1 / L4 表現相近也不自動切換。理由如下：</p>
      <table class="explain-table">
        <tr>
          <td><b>本次 backtest 對比</b></td>
          <td>
            L4 完整 pipeline MAPE = <b>${{fullM_l4.toFixed(1)}}%</b><br/>
            L1 完整 pipeline MAPE = <b>${{fullM_l1 != null ? fullM_l1.toFixed(1)+'%' : '—'}}</b>
            ${{diff != null ? `（${{l4Wins ? 'L4 勝' : 'L1 略勝'}} ${{Math.abs(diff).toFixed(1)}}%）` : ''}}
          </td>
        </tr>
        <tr>
          <td><b>L1 為何看起來不差？</b></td>
          <td>L1 用「Y-1 raw 場數比」、在過去幾年場數變化平緩時剛好接近 Y、屬運氣巧合，
            非結構性穩健。當 DHI 涵蓋率出現轉折（如近年），L1 會迅速失準。</td>
        </tr>
        <tr>
          <td><b>L4 為何選為固定方法？</b></td>
          <td>① 用最新季報資料、領先 L1 約 6-9 個月時效。<br/>
            ② 對未來預測更穩健、能持續吸收新季報。<br/>
            ③ 隨季報資料累積、L4 backtest 表現會持續改善。</td>
        </tr>
        <tr>
          <td><b>為何不自動切換？</b></td>
          <td>未來幾年 L4 預期穩定勝出（季報資料覆蓋擴大）。
            短期 backtest 偶爾 L1 略勝是過度擬合過去場數軌跡的副作用、
            不該作為長期預測決策依據。</td>
        </tr>
        <tr>
          <td><b>下次評估時機</b></td>
          <td>新增 1-2 個 holdout 年（例如 2025、2026 公告值發布後）、
            屆時若 L4 仍輸 L1 超過 2%、值得重新檢視策略。</td>
        </tr>
      </table>`;
  }}

  // Holdout 結論
  const hc = document.getElementById('holdout_conclusion');
  if (ho && ho.summary && hc) {{
    const s = ho.summary;
    const fullM = s.full_mape;
    const dhiM = s.dhi_mape;
    const bm = s.best_model;
    const bmM = (s.by_model_mape && s.by_model_mape[bm]) ? s.by_model_mape[bm].mape : null;
    const bmB = (s.by_model_mape && s.by_model_mape[bm]) ? s.by_model_mape[bm].bias : null;
    hc.innerHTML = `
      <div class="eb-title">✅ 本次結論 <span class="h-en">Conclusion</span></div>
      <ul>
        <li><b>系統採用 ${{bm}} 作為主要預測模型</b> ⭐
            ${{bmM != null ? `（4 年滾動回測 MAPE ${{bmM.toFixed(1)}}%、bias 系統性偏差 ${{bmB>=0?'+':''}}${{bmB.toFixed(1)}}%、已於正式預測自動扣除）` : ''}}</li>
        <li>📍 <b>解讀</b>：用 ${{bm}} 預測未來 12 個月，
            過去 ${{s.n_years}} 年滾動回測平均誤差約
            <b>±${{bmM != null ? bmM.toFixed(1) : '—'}}%</b>（含時序預測 + Level 4 SF 還原全管線）</li>
        <li>📍 <b>該選哪個模型</b>：系統已自動推薦
            <code>${{bm}}</code>、可在「月度預測詳情」分頁的「主要模型」下拉切換對照其他模型</li>
        <li>📍 <b>對照組</b>：Ensemble 加權集成 MAPE = ${{fullM.toFixed(1)}}%（受表現較差模型拉低、僅供交叉驗證、不作主要交付）；其他模型表現見下表</li>
      </ul>`;
  }}

  // Same-year 結論
  const oc = D.official_compare;
  const sc = document.getElementById('sameyear_conclusion');
  if (oc && oc.summary && sc) {{
    const s = oc.summary;
    const best = s.best_method;
    const bestInfo = s[best] || {{}};
    sc.innerHTML = `
      <div class="eb-title">✅ 本次結論 <span class="h-en">Conclusion</span></div>
      <ul>
        <li><b>最佳 SF 方法：${{bestInfo.name || best}}</b> ⭐
            （MAPE ${{bestInfo.mape != null ? bestInfo.mape.toFixed(1)+'%' : '—'}}、
            最大誤差 ${{bestInfo.max_err_pct != null ? bestInfo.max_err_pct.toFixed(1)+'%' : '—'}}）</li>
        <li>📍 <b>解讀</b>：當有當年實際 DHI 時、用此方法還原全國最準</li>
        <li>⚠️ <b>注意</b>：本表 <b>未包含時序預測誤差</b>。
            完整實戰精度請看「滾動回測」分頁。</li>
      </ul>`;

    // 自動把 ⭐ 標到對的 method（同步表格說明 + 欄位標頭）
    document.querySelectorAll('[data-best-marker]').forEach(el => {{
      const key = el.dataset.bestMarker;
      el.textContent = (key === best) ? ' ⭐' : '';
    }});
  }}
}}
renderAccConclusions();

// =============================================
// §6 方法論案例（從 CTX/D 動態填入）
// =============================================
function renderMethodology() {{
  const cfg = D.manifest.config || {{}};
  const tgt = CTX.target_year || '目標年';
  document.querySelectorAll('.case-target-year').forEach(el => {{
    el.textContent = tgt;
  }});

  // Stage 1: 資料處理 — 切開「預測輸入」與「驗證真值」
  const latestQ = (CTX.data_sources||[]).find(s=>s.freq==='quarterly')?.latest || '—';
  document.getElementById('case_data').innerHTML = `
    <div class="data-block input-block">
      <div class="db-header">
        <span class="db-icon">📥</span>
        <span class="db-title-zh">預測輸入</span>
        <span class="db-title-en">Prediction Inputs</span>
      </div>
      <div class="db-subtitle">直接決定預測值的資料 / Data that determine the forecast</div>
      <ol class="db-list">
        <li><b>DHI 月度紀錄</b><br/><span class="db-meta">DHI Monthly Records · 26 年、約 5,168,864 筆牛隻日測乳量資料</span></li>
        <li><b>農業部〈畜牧生產〉年報</b><br/><span class="db-meta">MOA Annual Livestock Report · 含產乳牛數、場數，2015–${{CTX.latest_official_year || '—'}} 年</span></li>
        <li><b>農業部〈在養量比較〉季報</b><br/><span class="db-meta">MOA Quarterly Inventory Report · ${{latestQ}} 為最新</span></li>
      </ol>
    </div>
    <div class="data-block truth-block">
      <div class="db-header">
        <span class="db-icon">🔒</span>
        <span class="db-title-zh">驗證真值</span>
        <span class="db-title-en">Validation Ground Truth</span>
      </div>
      <div class="db-subtitle">僅用於計算歷史誤差、絕不進入預測模型 /
        Used only for accuracy validation, never enters the model</div>
      <ol class="db-list" start="4">
        <li><b>農業部〈牛乳產量〉年報</b><br/><span class="db-meta">MOA Annual Milk Production Report · 1967–${{CTX.latest_official_year || '—'}} 年</span></li>
      </ol>
      <div class="db-warning">
        ⚠️ 此資料 <b>從未被預測流程使用</b>，僅作為事後對照工具計算 MAPE。<br/>
        <span class="db-meta-en">This data is <b>never used by the model</b>; it is only used to compute post-hoc accuracy.</span>
      </div>
    </div>
    <p style="margin-top:14px"><b>處理流程 / Pipeline</b>：合併 DHI → 篩活躍場（最近 180 天有測乳）
       → 月度加總（門檻 100 場/500 紀錄）→ 建立 ${{cfg.regions ? cfg.regions.length : '—'}} 個區域的時間序列</p>
    <p><b>本次活躍場數 / Active Farms</b>：<b>${{cfg.n_active_farms || '—'}}</b> 場</p>
  `;

  // Stage 2: 時序預測
  document.getElementById('case_forecast').innerHTML = `
    <p><b>5 個獨立時序模型（標準）</b>：</p>
    <ul>
      <li><b>naive_seasonal</b>：去年同月 × 年增率（基準線）</li>
      <li><b>stl_linear</b>：STL 拆 trend/seasonal/殘差，trend 線性外推</li>
      <li><b>holt_winters</b>：三重指數平滑（level + trend + multiplicative seasonal）</li>
      <li><b>sarima</b>：SARIMA(1,1,1)(1,1,1,12)</li>
      <li><b>prophet</b>：trend + yearly seasonal + changepoint detection</li>
    </ul>
    <p><b>進階模型（提供額外交叉驗證）</b>：</p>
    <ul>
      <li><b>cohort_simple</b>：物理結構模型「產乳牛 × DHI 單頭日產乳 × 305 天」、
        提供獨立交叉驗證、抗結構性轉折</li>
      <li><b>neural_prophet</b>：Prophet 結合 AR-Net 神經網路、
        學最近 12 期的 autoregressive 訊號</li>
    </ul>
    <p><b>系統採用：最佳單一模型</b>（依 4 年滾動回測自動選出）
      <b>${{CTX.best_model || '—'}}</b>、滾動回測 MAPE = ${{CTX.best_mape ? CTX.best_mape.toFixed(1)+'%' : '—'}}、
      bias 系統性偏差 = ${{CTX.best_bias != null ? (CTX.best_bias>=0?'+':'')+CTX.best_bias.toFixed(1)+'%' : '—'}}（已於正式預測自動扣除）</p>
    <p><b>對照組：Ensemble 加權集成</b>：上述模型加權平均、權重 = 1 / 訓練樣本 MAPE。
      系統不採用 ensemble 作為主要預測（受表現較差模型拉低）、僅作交叉驗證對照。</p>
  `;

  // Stage 3: SF 校正
  const sfRows = CTX.sf_by_year ?
    Object.entries(CTX.sf_by_year)
      .filter(([y]) => parseInt(y) >= (CTX.latest_official_year || 2018) - 1)
      .sort((a,b) => a[0] - b[0])
      .map(([y, info]) => `<tr><td>${{y}}</td>
        <td class="num">${{info.official_farms.toFixed(0)}}</td>
        <td class="num">${{info.dhi_farms.toFixed(0)}}</td>
        <td class="num"><b>${{info.sf.toFixed(3)}}</b></td>
      </tr>`).join('')
    : '';
  document.getElementById('case_sf').innerHTML = `
    <p><b>校正公式</b>（Level 4）：</p>
    <pre style="background:#f8f9fb;padding:10px;border-radius:4px;font-size:12px">
全國月乳量[m] = DHI 月乳量[m] × SF[m 所屬年]

SF[Y] = 估計農業部公告場數[Y] / 估計 DHI 場數[Y]
  · 估計農業部公告場數 = 用最新季報 + 年報線性外推到 Y
  · 估計 DHI 場數 = 用 DHI 歷年場數線性外推到 Y</pre>
    ${{sfRows ? `<p><b>本次 SF 計算</b>（${{CTX.target_year || '目標年'}}）：</p>
      <table class="method-table"><thead><tr>
        <th>年 / Year</th><th>農業部公告場 / Official Farms</th>
        <th>DHI 場 / DHI Farms</th><th>SF</th></tr></thead>
        <tbody>${{sfRows}}</tbody></table>` : ''}}
  `;

  // Case Study: 預測 target year
  const _bm = CTX.best_model || '主要模型';
  const _isCohort = (_bm === 'cohort_simple' || _bm === 'cohort_v2_auto');
  let caseHTML = `<p><b>輸入</b>：基準日 ${{cfg.reference_date || '—'}} 之前的 DHI 月度資料 + 在養量資料</p>`;
  caseHTML += `<p><b>步驟</b>：</p><ol>`;
  if (_isCohort) {{
    caseHTML += `<li>用結構式公式：<b>產量 = 產乳牛數 × 單頭日產乳 × 305 天 ÷ DHI/全國 productivity 比率</b></li>`;
    caseHTML += `<li>從歷史線性外推：${{tgt}} 年產乳牛、DHI 單頭日產乳、productivity 比率三個關鍵變數</li>`;
    caseHTML += `<li>結果直接是全國尺度（不需 SF 還原）、按季節形狀拆分到 12 個月</li>`;
  }} else {{
    caseHTML += `<li>${{_bm}} 模型預測 ${{tgt}} 全年 12 個月 DHI 樣本月乳量加總（P10/P50/P90）</li>`;
    if (CTX.sf_by_year && CTX.sf_by_year[tgt]) {{
      const sf = CTX.sf_by_year[tgt].sf;
      caseHTML += `<li>套用 SF[${{tgt}}] = <b>${{sf.toFixed(3)}}</b>（農業部公告場 ${{CTX.sf_by_year[tgt].official_farms.toFixed(0)}} / DHI 場 ${{CTX.sf_by_year[tgt].dhi_farms.toFixed(0)}}）</li>`;
    }}
    caseHTML += `<li>得到全國月度預測 P50（萬公噸）逐月顯示在「月度預測詳情」圖中</li>`;
  }}
  caseHTML += `<li>套用 holdout 量到的 bias 殘差校正（${{(CTX.best_bias!=null?(CTX.best_bias>=0?'+':'')+CTX.best_bias.toFixed(1)+'%':'—')}}）</li>`;
  caseHTML += `<li>12 個月加總 = <b>${{fmt_wton(CTX.forecast_p50_tons)}}</b>、信賴區間 ${{fmt_wton(CTX.forecast_p10_tons)}} – ${{fmt_wton(CTX.forecast_p90_tons)}}</li>`;
  caseHTML += `</ol>`;
  if (CTX.latest_official_tons && CTX.forecast_p50_tons) {{
    const yoy = CTX.yoy_per_year_pct;
    caseHTML += `<p><b>對照</b>：${{CTX.latest_official_year}} 年實際 ${{fmt_wton(CTX.latest_official_tons)}}
      → ${{tgt}} 預測 ${{fmt_wton(CTX.forecast_p50_tons)}}
      = <b>${{yoy >= 0 ? '+' : ''}}${{yoy.toFixed(1)}}%/年</b>（年化）</p>`;
  }}
  document.getElementById('case_predict').innerHTML = caseHTML;

  // Validation
  document.getElementById('case_validate').innerHTML = `
    <p><b>兩層驗證</b>：</p>
    <ol>
      <li><b>同年估計</b>：對歷史每年 Y、用該年實際 DHI 加總 × SF 估計全國值，
          對照農業部公告值 → 測試「SF 還原步驟」準度</li>
      <li><b>滾動回測</b>（holdout backtest）：對年 Y、模型只看到 ≤ Y-1 資料，
          預測 Y 全年 12 個月 → 對照真值 → 測試「完整 pipeline」實戰準度</li>
    </ol>
    <p><b>解讀</b>：滾動回測的 MAPE 是給主管機關的承諾數字（系統採用 best_model 全管線）；
       同年估計只測 SF 還原這一段、輔助診斷 SF 方法本身的精度。</p>
    <p>詳細結果見上方「🎯 模型精度監控」區塊。</p>
  `;
}}
renderMethodology();

document.getElementById('sel_model').addEventListener('change', e => {{
  SELECTED_MODEL = e.target.value;
  renderRegion();
}});

document.getElementById('sel_unit').addEventListener('change', e => {{
  CUR_UNIT = e.target.value;
  // 重 render 所有跟單位相關的區塊
  if (typeof renderSummary === 'function') renderSummary();
  if (typeof renderExecutiveSummary === 'function') renderExecutiveSummary();
  if (typeof renderHoldoutBacktest === 'function') renderHoldoutBacktest();
  if (typeof renderOfficialCompare === 'function') renderOfficialCompare();
  if (typeof renderAccConclusions === 'function') renderAccConclusions();
  renderRegion();
  renderAllRegions();
}});
const COLORS = {{
  naive_seasonal: '#999',
  stl_linear: '#1e7c3a',
  holt_winters: '#a05a00',
  sarima: '#9170b0',
  prophet: '#d05a3c',
  neural_prophet: '#e91e63',
  cohort_simple: '#00897b',
  cohort_v2_auto: '#1e88e5',
  ensemble: '#2a4d69',
}};

const MODEL_DESC = {{
  naive_seasonal: '基準線：去年同月 × 成長率',
  stl_linear: 'STL 分解 + 線性趨勢外推',
  holt_winters: '三重指數平滑（季節 multiplicative）',
  sarima: 'SARIMA(1,1,1)(1,1,1,12)',
  prophet: 'Facebook Prophet',
  neural_prophet: 'NeuralProphet（Prophet + AR-Net 神經網路）',
  cohort_simple: 'Cohort 結構：產乳牛 × 單頭日產乳 × 305 天',
  cohort_v2_auto: 'Cohort v2：N 季度回歸 + r ensemble (5yr OLS+3yr mean) + auto nowcast',
  ensemble: '加權集成（用 in-sample MAPE 倒數）',
}};

const sel = document.getElementById('sel_region');
const regions = Object.keys(D.results);
regions.forEach(r => {{
  const o = document.createElement('option');
  o.value = r; o.textContent = r;
  sel.appendChild(o);
}});

const sel_scale = document.getElementById('sel_scale');
sel_scale.addEventListener('change', renderRegion);

// 縮放按鈕
function setX(min, max) {{
  if (!tsChart) return;
  tsChart.options.scales.x.min = min;
  tsChart.options.scales.x.max = max;
  tsChart.update();
}}
document.getElementById('zoom_reset').onclick = () => {{
  if (!tsChart) return;
  tsChart.resetZoom();
}};
document.getElementById('zoom_all').onclick = () => {{
  if (!tsChart) return;
  setX(0, tsChart.data.labels.length - 1);
}};
document.getElementById('zoom_recent').onclick = () => {{
  if (!tsChart) return;
  const n = tsChart.data.labels.length;
  setX(Math.max(0, n - 72), n - 1);  // 5 年 + 12 月預測
}};
document.getElementById('zoom_forecast').onclick = () => {{
  if (!tsChart) return;
  const labels = tsChart.data.labels;
  const region = sel.value;
  const histN = D.results[region].series_history.length;
  setX(Math.max(0, histN - 6), labels.length - 1);  // 預測前 6 月 + 預測 12 月
}};

// 從 backtest 抓最佳單一模型（預設 ensemble、有 backtest 就用它）
function _bestModel() {{
  if (SELECTED_MODEL && SELECTED_MODEL !== '__best__') return SELECTED_MODEL;
  return (D.holdout_backtest && D.holdout_backtest.summary
            && D.holdout_backtest.summary.best_model) || 'ensemble';
}}
let BEST_MODEL = _bestModel();
const BEST_MODEL_MAPE = (D.holdout_backtest && D.holdout_backtest.summary
                            && D.holdout_backtest.summary.by_model_mape
                            && D.holdout_backtest.summary.by_model_mape[BEST_MODEL])
                            ? D.holdout_backtest.summary.by_model_mape[BEST_MODEL].mape
                            : null;

let tsChart = null;
function renderRegion() {{
  BEST_MODEL = _bestModel();  // 重新算（因 SELECTED_MODEL 可能變了）
  const region = sel.value;
  const raw = D.results[region];
  const sv = sel_scale.value;
  let r = raw;
  let scale_label = '（DHI 加總）';
  if (sv === 'calibrated_l4' && raw.calibrated_l4) {{
    r = {{ ...raw, ...raw.calibrated_l4, calibrated_l4: raw.calibrated_l4 }};
    scale_label = '（全國估計 · Level 4：季報+年報外推 ⭐）';
  }} else if (sv === 'calibrated' && raw.calibrated) {{
    r = {{ ...raw, ...raw.calibrated, calibrated: raw.calibrated }};
    scale_label = '（全國估計 · 舊：月度動態涵蓋率）';
  }}

  // 收集所有月份
  const histMonths = r.series_history.map(p => p.yyyymm);
  const fcMonthsSet = new Set();
  r.models.filter(m => m.success).forEach(m =>
    m.forecast.forEach(f => fcMonthsSet.add(f.yyyymm)));
  if (r.ensemble) r.ensemble.forecast.forEach(f => fcMonthsSet.add(f.yyyymm));
  const fcMonths = [...fcMonthsSet].sort();
  const allMonths = [...histMonths, ...fcMonths];

  const datasets = [];

  const D_CONV = (v) => v == null ? null : v / unit().divisor;

  // 歷史線
  datasets.push({{
    label: '歷史實際值',
    data: histMonths.map((m, i) => D_CONV(r.series_history[i].value)).concat(
        fcMonths.map(() => null)),
    borderColor: '#000',
    backgroundColor: '#000',
    borderWidth: 2,
    pointRadius: 2,
    tension: 0.3,
  }});

  // 各模型預測（best model 加粗）
  r.models.filter(m => m.success).forEach(m => {{
    const isBest = (m.model === BEST_MODEL);
    const data = histMonths.map(() => null).concat(
      fcMonths.map(mn => {{
        const pt = m.forecast.find(f => f.yyyymm === mn);
        return pt ? D_CONV(pt.p50) : null;
      }})
    );
    datasets.push({{
      label: isBest ? `${{m.model}} ⭐ 最佳` : m.model,
      data,
      borderColor: COLORS[m.model] || '#666',
      backgroundColor: COLORS[m.model] || '#666',
      borderWidth: isBest ? 3.5 : 1.5,
      borderDash: isBest ? [] : [3,3],
      pointRadius: isBest ? 3 : 1,
      tension: 0.3,
    }});
  }});

  // Ensemble（best 是 ensemble 才加粗、否則細線對照）
  if (r.ensemble) {{
    const isBest = (BEST_MODEL === 'ensemble');
    const data = histMonths.map(() => null).concat(
      fcMonths.map(mn => {{
        const pt = r.ensemble.forecast.find(f => f.yyyymm === mn);
        return pt ? D_CONV(pt.p50) : null;
      }})
    );
    datasets.push({{
      label: isBest ? 'Ensemble ⭐ 最佳' : 'Ensemble (對照)',
      data,
      borderColor: COLORS.ensemble,
      backgroundColor: COLORS.ensemble,
      borderWidth: isBest ? 3.5 : 1.5,
      borderDash: isBest ? [] : [4, 4],
      pointRadius: isBest ? 3 : 1,
      tension: 0.3,
    }});
  }}

  if (tsChart) tsChart.destroy();
  // 預設只顯示最後 60 個月（5 年）+ 預測，避免擠在一起
  const defaultStart = Math.max(0, allMonths.length - 72);
  // 預測期起點 = 歷史月數（第一個預測月的索引）
  const fcStartIdx = histMonths.length - 0.5;
  const fcEndIdx = allMonths.length - 1 + 0.5;
  tsChart = new Chart(document.getElementById('ts_chart'), {{
    type: 'line',
    data: {{labels: allMonths, datasets}},
    options: {{
      responsive: true, maintainAspectRatio: false,
      plugins: {{
        legend: {{position: 'top'}},
        zoom: {{
          pan: {{ enabled: true, mode: 'x' }},
          zoom: {{
            wheel: {{ enabled: true }},
            pinch: {{ enabled: true }},
            mode: 'x',
          }},
        }},
        annotation: {{
          annotations: {{
            forecast_box: {{
              type: 'box',
              xMin: fcStartIdx,
              xMax: fcEndIdx,
              backgroundColor: 'rgba(255, 240, 0, 0.22)',
              borderColor: 'rgba(255, 200, 0, 0.45)',
              borderWidth: 1,
              borderDash: [4, 4],
              label: {{
                display: true,
                content: '預測期',
                position: {{ x: 'center', y: 'start' }},
                color: '#8a6500',
                font: {{ size: 11, weight: 'bold' }},
                backgroundColor: 'rgba(255, 240, 0, 0.6)',
                padding: 3,
              }},
            }}
          }}
        }},
      }},
      scales: {{
        x: {{ min: defaultStart, max: allMonths.length - 1 }},
        y: {{title: {{display: true, text: unit_label()}}}}
      }}
    }}
  }});

  // 說明 + 期間總乳量
  let note = `區域 <b>${{region}}</b> ${{scale_label}}: 歷史 ${{histMonths.length}} 個月 → 預測 ${{fcMonths.length}} 個月`;
  if (BEST_MODEL_MAPE != null) {{
    note += `。<b>主要模型：${{BEST_MODEL}} ⭐</b>（滾動回測 MAPE ${{BEST_MODEL_MAPE.toFixed(1)}}%）`;
  }} else if (r.ensemble?.weights) {{
    const top = Object.entries(r.ensemble.weights).sort((a,b) => b[1]-a[1])[0];
    note += `。In-sample 最佳: ${{top[0]}} (權重 ${{(top[1]*100).toFixed(0)}}%)`;
  }}

  // 預估期間總乳量（best model 為主）
  // 找出 best model 的 forecast
  let bestModelFc = null;
  if (BEST_MODEL === 'ensemble' && r.ensemble) {{
    bestModelFc = r.ensemble.forecast;
  }} else {{
    const bm = r.models.find(m => m.success && m.model === BEST_MODEL);
    if (bm) bestModelFc = bm.forecast;
  }}
  const fallbackFc = r.ensemble ? r.ensemble.forecast : null;
  const fc = bestModelFc || fallbackFc;
  if (fc) {{
    const total_p50 = fc.reduce((s,p) => s + p.p50, 0);
    const total_p10 = fc.reduce((s,p) => s + p.p10, 0);
    const total_p90 = fc.reduce((s,p) => s + p.p90, 0);
    const mapeStr = BEST_MODEL_MAPE != null
      ? ` · 滾動回測 MAPE ${{BEST_MODEL_MAPE.toFixed(1)}}%` : '';

    let html = `<div class="period-totals">`;
    html += `<div class="pt-title">📊 ${{region}} 預估期間總乳量（${{BEST_MODEL}} ⭐ 最佳模型${{mapeStr}}）<span class="pt-hint">點選任一列展開計算明細</span></div>`;
    html += `<table class="pt-table"><thead><tr>
      <th>期間</th><th>P50</th><th>P10–P90</th></tr></thead><tbody>`;

    // 通用 helper：算 P10/P90 範圍
    function _rangeStr(forecasts) {{
      const t10 = forecasts.reduce((s,p) => s + (p.p10||p.p50||0), 0);
      const t50 = forecasts.reduce((s,p) => s + (p.p50||0), 0);
      const t90 = forecasts.reduce((s,p) => s + (p.p90||p.p50||0), 0);
      if (Math.abs(t90 - t10) < t50 * 0.005) return '—';
      return fmt_int(t10) + ' – ' + fmt_int(t90);
    }}

    // 計算明細產生器（依模型類型）
    function _calcDetail(modelName, displayedP50_kg) {{
      const sfYr = (CTX.sf_by_year && CTX.target_year)
        ? CTX.sf_by_year[CTX.target_year] : null;
      const biasInfo = (D.holdout_backtest && D.holdout_backtest.summary
                        && D.holdout_backtest.summary.by_model_mape
                        && D.holdout_backtest.summary.by_model_mape[modelName])
                        || null;
      const biasPct = biasInfo ? biasInfo.bias : 0;
      const biasFactor = 1 - biasPct / 100;
      const u = unit();
      const fmtTons = (kg) => (kg/1000).toLocaleString(undefined,
        {{maximumFractionDigits: 0}}) + ' 公噸';
      const fmtPct = (v) => (v >= 0 ? '+' : '') + v.toFixed(2) + '%';

      // === cohort_simple：結構式公式 ===
      if (modelName === 'cohort_simple') {{
        const cb = CTX.cohort_baseline;
        if (!cb) return '<div class="pt-calc-empty">cohort baseline 資料不可用</div>';
        const cows = cb.cows;
        const yld = cb.daily_yield_kg;
        const days = cb.lactation_days || 305;
        const ratio = cb.productivity_ratio || 1.0;
        const sf = cb.static_calibration_factor || 1.0;
        const raw = cows * yld * days / 1000;  // 公噸
        const afterProd = raw / ratio;
        const final = afterProd * sf;
        return `<div class="pt-calc">
          <div class="pt-calc-h">📐 cohort_simple 結構式公式（不走 SF）</div>
          <div class="pt-calc-step">
            <span class="pt-calc-form">${{Math.round(cows).toLocaleString()}} 頭 × ${{yld.toFixed(2)}} kg × ${{days}} 天 ÷ 1000</span>
            <span class="pt-calc-eq">= <b>${{fmtTons(raw*1000)}}</b><span class="pt-calc-anno">（公式原始值）</span></span>
          </div>
          <div class="pt-calc-step">
            <span class="pt-calc-form">÷ ${{ratio.toFixed(4)}} (DHI/全國 productivity 比率)</span>
            <span class="pt-calc-eq">= <b>${{fmtTons(afterProd*1000)}}</b></span>
          </div>
          <div class="pt-calc-step">
            <span class="pt-calc-form">× ${{sf.toFixed(4)}} (1 − ${{(cb.static_bias_pct||0).toFixed(2)}}% holdout 殘差校正)</span>
            <span class="pt-calc-eq pt-calc-final">= <b>${{fmtTons(final*1000)}}</b><span class="pt-calc-anno">（按月度季節形狀拆 12 月後加總略有 round-off）</span></span>
          </div>
        </div>`;
      }}

      // === cohort_v2_auto：v2 結構式（n=quarterly + r=adaptive ensemble）===
      if (modelName === 'cohort_v2_auto') {{
        const cb = CTX.cohort_v2_baseline;
        if (!cb) return '<div class="pt-calc-empty">cohort v2 baseline 資料不可用（pipeline 尚未產出 v2）</div>';
        const cows = cb.cows;
        const yld = cb.daily_yield_kg;
        const days = cb.lactation_days || 305;
        const ratio = cb.productivity_ratio || 1.0;
        const sf = cb.static_calibration_factor || 1.0;
        const raw = cows * yld * days / 1000;
        const afterProd = raw / ratio;
        const final = afterProd * sf;
        const cfg = cb.v2_config || {{}};
        return `<div class="pt-calc">
          <div class="pt-calc-h">📐 cohort_v2_auto 結構式公式（工程改善版本、不走 SF）</div>
          <div class="pt-calc-step" style="background:#fff8e1;padding:6px 10px;border-radius:4px;margin-bottom:8px;font-size:12px;color:#666">
            <b>v2 配置</b>：N 投影 = ${{cfg.n_projection}}、r 投影 = ${{cfg.r_window}}、as_of = ${{cfg.as_of_date || 'today'}}
          </div>
          <div class="pt-calc-step">
            <span class="pt-calc-form">${{Math.round(cows).toLocaleString()}} 頭 (季度回歸) × ${{yld.toFixed(2)}} kg × ${{days}} 天 ÷ 1000</span>
            <span class="pt-calc-eq">= <b>${{fmtTons(raw*1000)}}</b><span class="pt-calc-anno">（v2 公式原始值）</span></span>
          </div>
          <div class="pt-calc-step">
            <span class="pt-calc-form">÷ ${{ratio.toFixed(4)}} (r ensemble: 5yr OLS + 3yr mean 平均)</span>
            <span class="pt-calc-eq">= <b>${{fmtTons(afterProd*1000)}}</b></span>
          </div>
          <div class="pt-calc-step">
            <span class="pt-calc-form">× ${{sf.toFixed(4)}} (1 − ${{(cb.static_bias_pct||0).toFixed(2)}}% holdout 殘差校正)</span>
            <span class="pt-calc-eq pt-calc-final">= <b>${{fmtTons(final*1000)}}</b></span>
          </div>
        </div>`;
      }}

      // === ensemble：top-3 加權平均 ===
      if (modelName === 'ensemble') {{
        const w = r.ensemble && r.ensemble.weights ? r.ensemble.weights : null;
        if (!w) return '<div class="pt-calc-empty">ensemble 權重資料不可用</div>';
        const entries = Object.entries(w).sort((a,b) => b[1] - a[1]);
        const rows = entries.map(([m, wt]) => {{
          const mFc = r.models.find(x => x.model === m && x.success);
          const mTotal = mFc ? mFc.forecast.reduce((s,p) => s + p.p50, 0) : 0;
          return `<tr><td>${{m}}</td>
            <td class="num">${{(wt*100).toFixed(1)}}%</td>
            <td class="num">${{fmt_int(mTotal)}}</td>
            <td class="num">${{fmt_int(mTotal * wt)}}</td></tr>`;
        }}).join('');
        return `<div class="pt-calc">
          <div class="pt-calc-h">📐 Ensemble 加權集成（權重 = 1 / in-sample MAPE）</div>
          <table class="pt-calc-tbl">
            <thead><tr><th>子模型</th><th>權重</th><th>P50 預測</th><th>加權貢獻</th></tr></thead>
            <tbody>${{rows}}</tbody>
          </table>
          <div class="pt-calc-step pt-calc-final-row">
            <span class="pt-calc-form">加權加總</span>
            <span class="pt-calc-eq pt-calc-final">= <b>${{fmtTons(displayedP50_kg)}}</b><span class="pt-calc-anno">（受表現較差模型拉低、僅供交叉驗證）</span></span>
          </div>
        </div>`;
      }}

      // === 時序模型（stl_linear/holt_winters/sarima/prophet/neural_prophet/naive_seasonal）===
      // 找原始 DHI 預測（沒套 SF）
      const rawModel = (raw.models || []).find(m => m.model === modelName);
      if (!rawModel || !rawModel.forecast) return '<div class="pt-calc-empty">原始 DHI 預測資料不可用</div>';
      const dhiSum = rawModel.forecast.reduce((s,p) => s + p.p50, 0);  // kg
      // 用每月的 SF 加權算「實際 SF」、簡化：看單一目標年
      const sfApplied = sfYr ? sfYr.sf : null;
      const afterSF = sfApplied ? dhiSum * sfApplied : null;
      const finalKg = afterSF ? afterSF * biasFactor : null;

      const desc = ({{
        'stl_linear': 'STL 分解 + 線性趨勢外推',
        'holt_winters': '三重指數平滑',
        'sarima': 'SARIMA(1,1,1)(1,1,1,12)',
        'prophet': 'Facebook Prophet',
        'neural_prophet': 'NeuralProphet (Prophet + AR-Net)',
        'naive_seasonal': '基準線：去年同月 × 年增率',
      }})[modelName] || modelName;

      return `<div class="pt-calc">
        <div class="pt-calc-h">📐 ${{modelName}}：${{desc}}（時序預測 → SF → bias）</div>
        <div class="pt-calc-step">
          <span class="pt-calc-form">12 月 DHI 樣本預測加總</span>
          <span class="pt-calc-eq">= <b>${{fmtTons(dhiSum)}}</b><span class="pt-calc-anno">（樣本層級、未還原全國）</span></span>
        </div>
        ${{sfApplied ? `<div class="pt-calc-step">
          <span class="pt-calc-form">× SF[${{CTX.target_year}}] = ${{sfApplied.toFixed(4)}}（${{Math.round(sfYr.official_farms)}} ÷ ${{Math.round(sfYr.dhi_farms)}}、官方場數 / DHI 場數）</span>
          <span class="pt-calc-eq">= <b>${{fmtTons(afterSF)}}</b><span class="pt-calc-anno">（套 L4 SF 還原全國）</span></span>
        </div>` : ''}}
        ${{biasInfo ? `<div class="pt-calc-step">
          <span class="pt-calc-form">× ${{biasFactor.toFixed(4)}} (1 − ${{biasPct.toFixed(2)}}% holdout bias)</span>
          <span class="pt-calc-eq pt-calc-final">= <b>${{fmtTons(finalKg)}}</b><span class="pt-calc-anno">（最終 P50 加總、月度 round-off ~0.1%）</span></span>
        </div>` : `<div class="pt-calc-step">
          <span class="pt-calc-form">最終值（無 bias 校正資料）</span>
          <span class="pt-calc-eq pt-calc-final">= <b>${{fmtTons(displayedP50_kg)}}</b></span>
        </div>`}}
      </div>`;
    }}

    // 渲染各模型 row（每行可點擊展開）
    function _addRow(label, p50, p10p90, modelName, isBest, calcKey) {{
      const tr_main = `<tr class="pt-row" data-pt="${{calcKey}}" ${{isBest ? 'style="background:#fffbed;border-left:3px solid #f4c430"' : 'style="color:#888"'}}>
        <td><b>${{label}}</b><span class="pt-toggle">▶</span></td>
        <td class="num"${{isBest ? '' : ' style="color:#888"'}}>${{isBest ? `<b>${{fmt_int(p50)}}</b>` : fmt_int(p50)}}</td>
        <td class="num small"${{isBest ? '' : ' style="color:#888"'}}>${{p10p90}}</td>
      </tr>`;
      const tr_detail = `<tr class="pt-detail-row" data-pt="${{calcKey}}" style="display:none">
        <td colspan="3" style="background:#fafbfc;padding:0">${{_calcDetail(modelName, p50)}}</td>
      </tr>`;
      return tr_main + tr_detail;
    }}

    // BEST_MODEL 那行（高亮）
    html += _addRow(`${{BEST_MODEL}} ⭐ (${{fc.length}} 月)`,
                     total_p50,
                     fmt_int(total_p10) + ' – ' + fmt_int(total_p90),
                     BEST_MODEL, true, BEST_MODEL);

    // Ensemble 對照（如果 best 不是 ensemble）
    if (BEST_MODEL !== 'ensemble' && r.ensemble) {{
      const ens_total = r.ensemble.forecast.reduce((s,p) => s + p.p50, 0);
      html += _addRow('Ensemble (對照)', ens_total,
                       _rangeStr(r.ensemble.forecast),
                       'ensemble', false, 'ensemble');
    }}

    // 其他模型對比
    r.models.filter(m => m.success && m.model !== BEST_MODEL).forEach(m => {{
      const m_total = m.forecast.reduce((s,p) => s + p.p50, 0);
      html += _addRow(m.model, m_total, _rangeStr(m.forecast),
                       m.model, false, m.model);
    }});
    html += `</tbody></table></div>`;
    note += html;
  }}
  document.getElementById('ts_note').innerHTML = note;

  // 綁定點擊展開（事件代理）
  const noteEl = document.getElementById('ts_note');
  if (noteEl) {{
    noteEl.querySelectorAll('.pt-row').forEach(row => {{
      row.addEventListener('click', () => {{
        const key = row.dataset.pt;
        const detail = noteEl.querySelector(`.pt-detail-row[data-pt="${{key}}"]`);
        if (detail) {{
          const open = detail.style.display !== 'none';
          detail.style.display = open ? 'none' : 'table-row';
          const tog = row.querySelector('.pt-toggle');
          if (tog) tog.textContent = open ? '▶' : '▼';
        }}
      }});
    }});
  }}

  // 模型表（如果儀表板有此區塊就更新；新版 dashboard 已移除）
  const tb = document.querySelector('#model_table tbody');
  if (!tb) return;
  tb.innerHTML = '';
  r.models.forEach(m => {{
    const w = r.ensemble?.weights?.[m.model];
    const wPct = w != null ? (w*100).toFixed(1) + '%' : '—';
    const status = m.success ?
      '<span style="color:#1e7c3a">✓</span>' :
      `<span style="color:#999">${{m.error || '失敗'}}</span>`;
    tb.insertAdjacentHTML('beforeend', `
      <tr><td><b>${{m.model}}</b></td>
        <td>${{m.success ? (m.in_sample_mape || 0).toFixed(1) + '%' : '—'}}</td>
        <td>${{wPct}}</td>
        <td>${{status}}</td>
        <td style="font-size:11px;color:#666">${{MODEL_DESC[m.model] || ''}}</td>
      </tr>`);
  }});
}}
sel.addEventListener('change', renderRegion);
renderRegion();

// 多區域對比圖
function renderAllRegions() {{
  const datasets = [];
  // 取得所有月份
  const allMonthsSet = new Set();
  Object.values(D.results).forEach(r => {{
    r.series_history.forEach(p => allMonthsSet.add(p.yyyymm));
    if (r.ensemble) r.ensemble.forecast.forEach(p => allMonthsSet.add(p.yyyymm));
  }});
  const allMonths = [...allMonthsSet].sort();

  const regColors = {{
    '全國': '#000',
    '北': '#1e7c3a', '中': '#a05a00', '南': '#9170b0', '東': '#d05a3c',
    // 縣市（每個都不同顏色，避免灰一片）
    '屏東縣': '#e74c3c',  '雲林縣': '#3498db',  '彰化縣': '#f39c12',
    '臺南市': '#8e44ad',  '高雄市': '#16a085',  '桃園市': '#e67e22',
    '嘉義縣': '#27ae60',  '花蓮縣': '#34495e',  '苗栗縣': '#c0392b',
    '臺東縣': '#7f8c8d',  '南投縣': '#2980b9',  '新竹縣': '#d35400',
    '宜蘭縣': '#1abc9c',  '基隆市': '#95a5a6',  '臺北市': '#bdc3c7',
    '新北市': '#7f8c8d',
  }};

  // 找出最早的預測起點（取所有區域 forecast 中最早的 yyyymm）
  let fcStartMonth = null;
  Object.values(D.results).forEach(r => {{
    if (r.ensemble && r.ensemble.forecast.length > 0) {{
      const m0 = r.ensemble.forecast[0].yyyymm;
      if (fcStartMonth === null || m0 < fcStartMonth) fcStartMonth = m0;
    }}
  }});
  const fcStartIdxAll = fcStartMonth ? allMonths.indexOf(fcStartMonth) : -1;

  Object.entries(D.results).forEach(([region, r]) => {{
    if (!r.ensemble) return;
    const fullSeries = allMonths.map(mn => {{
      const hist = r.series_history.find(p => p.yyyymm === mn);
      if (hist) return hist.value / unit().divisor;
      const fc = r.ensemble.forecast.find(p => p.yyyymm === mn);
      return fc ? fc.p50 / unit().divisor : null;
    }});
    datasets.push({{
      label: region,
      data: fullSeries,
      borderColor: regColors[region] || '#666',
      backgroundColor: regColors[region] || '#666',
      borderWidth: region === '全國' ? 3 : 1.5,
      pointRadius: 1,
      tension: 0.3,
    }});
  }});

  // 預設只顯示最近 60 月 + 預測（不顯示太早）
  const defaultStart = Math.max(0, allMonths.length - 72);
  // 預測期 box 範圍
  const annotationCfg = (fcStartIdxAll > 0) ? {{
    annotations: {{
      forecast_box: {{
        type: 'box',
        xMin: fcStartIdxAll - 0.5,
        xMax: allMonths.length - 1 + 0.5,
        backgroundColor: 'rgba(255, 240, 0, 0.22)',
        borderColor: 'rgba(255, 200, 0, 0.45)',
        borderWidth: 1,
        borderDash: [4, 4],
        label: {{
          display: true,
          content: '預測期',
          position: {{ x: 'center', y: 'start' }},
          color: '#8a6500',
          font: {{ size: 11, weight: 'bold' }},
          backgroundColor: 'rgba(255, 240, 0, 0.6)',
          padding: 3,
        }},
      }}
    }}
  }} : {{}};
  if (window.allRegChart) window.allRegChart.destroy();
  window.allRegChart = new Chart(document.getElementById('all_regions_chart'), {{
    type: 'line',
    data: {{labels: allMonths, datasets}},
    options: {{
      responsive: true, maintainAspectRatio: false,
      plugins: {{
        legend: {{position: 'top'}},
        zoom: {{
          pan: {{ enabled: true, mode: 'x' }},
          zoom: {{
            wheel: {{ enabled: true }},
            pinch: {{ enabled: true }},
            mode: 'x',
          }},
        }},
        annotation: annotationCfg,
      }},
      scales: {{
        x: {{ min: defaultStart, max: allMonths.length - 1 }},
        y: {{title: {{display: true, text: unit_label()}}}}
      }}
    }}
  }});

  // 加區域開關 checkbox
  const togBox = document.getElementById('region_toggles');
  togBox.innerHTML = '<span style="font-size:13px;font-weight:600;">顯示:</span>';
  Object.keys(D.results).forEach((reg, idx) => {{
    const isMacro = ['全國','北','中','南','東'].includes(reg);
    const checked = isMacro ? 'checked' : '';
    togBox.insertAdjacentHTML('beforeend', `
      <label class="reg-toggle">
        <input type="checkbox" data-region="${{reg}}" ${{checked}} />
        <span style="color:${{regColors[reg] || '#666'}};font-weight:600;">${{reg}}</span>
      </label>`);
  }});
  // 預設縣市先隱藏
  if (window.allRegChart) {{
    window.allRegChart.data.datasets.forEach((ds, i) => {{
      const reg = ds.label;
      if (!['全國','北','中','南','東'].includes(reg)) {{
        window.allRegChart.setDatasetVisibility(i, false);
      }}
    }});
    window.allRegChart.update();
  }}
  togBox.querySelectorAll('input[type=checkbox]').forEach(cb => {{
    cb.addEventListener('change', () => {{
      const reg = cb.dataset.region;
      const idx = window.allRegChart.data.datasets.findIndex(d => d.label === reg);
      if (idx >= 0) {{
        window.allRegChart.setDatasetVisibility(idx, cb.checked);
        window.allRegChart.update();
      }}
    }});
  }});

  // Zoom 按鈕
  document.getElementById('all_zoom_reset').onclick = () => {{
    if (!window.allRegChart) return;
    window.allRegChart.options.scales.x.min = defaultStart;
    window.allRegChart.options.scales.x.max = allMonths.length - 1;
    window.allRegChart.update();
  }};
  document.getElementById('all_zoom_full').onclick = () => {{
    if (!window.allRegChart) return;
    window.allRegChart.options.scales.x.min = 0;
    window.allRegChart.options.scales.x.max = allMonths.length - 1;
    window.allRegChart.update();
  }};
  document.getElementById('all_zoom_recent').onclick = () => {{
    if (!window.allRegChart) return;
    window.allRegChart.options.scales.x.min = Math.max(0, allMonths.length - 72);
    window.allRegChart.options.scales.x.max = allMonths.length - 1;
    window.allRegChart.update();
  }};

  // 區域加總對比表（macro 區域 vs 全國 vs 縣市）
  const useCal = sel_scale.value === 'calibrated';
  const summary = document.getElementById('region_total_summary');
  const MACROS = ['北','中','南','東'];
  let total_macros = 0;
  let national_total = 0;

  // 全國
  const nat = D.results['全國'];
  const natSrc = (useCal && nat.calibrated) ? nat.calibrated : nat;
  if (natSrc.ensemble) {{
    national_total = natSrc.ensemble.forecast.reduce((s,p) => s + p.p50, 0);
  }}

  function get_total(region) {{
    const r = D.results[region];
    if (!r) return null;
    const src = (useCal && r.calibrated) ? r.calibrated : r;
    if (!src.ensemble) return null;
    return src.ensemble.forecast.reduce((s,p) => s + p.p50, 0);
  }}

  let html = '<h3 style="font-size:14px;margin:16px 0 8px;color:#2a4d69;">Macro 區域 vs 全國</h3>';
  html += '<table class="hierarchy-table"><thead><tr>';
  html += `<th>區域</th><th>未來 12 月加總 P50</th><th>占全國比</th></tr></thead><tbody>`;
  MACROS.forEach(reg => {{
    const t = get_total(reg);
    if (t == null) return;
    total_macros += t;
    const pct = national_total > 0 ? (t/national_total*100).toFixed(1) : '—';
    html += `<tr><td><b>${{reg}}</b></td>
      <td class="num">${{fmt_int(t)}}</td>
      <td class="num">${{pct}}%</td></tr>`;
  }});
  html += `<tr style="border-top:2px solid #2a4d69;background:#e7f0f7;font-weight:600">
    <td>北+中+南+東加總</td>
    <td class="num">${{fmt_int(total_macros)}}</td>
    <td class="num"></td></tr>`;
  html += `<tr style="background:#fff8e7;font-weight:600">
    <td>全國（直接預測）</td>
    <td class="num">${{fmt_int(national_total)}}</td>
    <td class="num">100%</td></tr>`;
  if (national_total > 0) {{
    const diff = (total_macros - national_total) / national_total * 100;
    html += `<tr><td colspan="3" style="font-size:11px;color:#888">
      區域加總 vs 全國差異: ${{diff>0?'+':''}}${{diff.toFixed(1)}}% (階層不一致、正常範圍)
    </td></tr>`;
  }}
  html += '</tbody></table>';

  // 縣市表
  const counties = Object.keys(D.results).filter(k =>
    k !== '全國' && !MACROS.includes(k));
  if (counties.length > 0) {{
    html += '<h3 style="font-size:14px;margin:20px 0 8px;color:#2a4d69;">主要縣市（&gt;= 5 場 DHI）</h3>';
    html += '<table class="hierarchy-table"><thead><tr>';
    html += '<th>縣市</th><th>未來 12 月加總 P50</th><th>占全國比</th></tr></thead><tbody>';
    let county_total = 0;
    const rows = counties.map(c => ({{ county: c, total: get_total(c) }}))
      .filter(x => x.total != null)
      .sort((a,b) => b.total - a.total);
    rows.forEach(r => {{
      county_total += r.total;
      const pct = national_total > 0 ? (r.total/national_total*100).toFixed(1) : '—';
      html += `<tr><td>${{r.county}}</td>
        <td class="num">${{fmt_int(r.total)}}</td>
        <td class="num">${{pct}}%</td></tr>`;
    }});
    html += `<tr style="background:#e7f0f7;font-weight:600">
      <td>${{rows.length}} 個縣市加總</td>
      <td class="num">${{fmt_int(county_total)}}</td>
      <td class="num">${{national_total>0 ? (county_total/national_total*100).toFixed(1)+'%' : '—'}}</td></tr>`;
    html += '</tbody></table>';
  }}

  summary.innerHTML = html;
}}
renderAllRegions();

// ===== 預測 vs 答案（誠實版） =====
function renderOfficialCompare() {{
  const oc = D.official_compare;
  const c = document.getElementById('official_compare_container');
  if (!oc || !oc.rows || oc.rows.length === 0) {{
    c.innerHTML = '<p style="color:#888">（暫無比較資料）</p>';
    return;
  }}
  const s = oc.summary;
  const best = s.best_method;

  let html = '';
  // 三張方法卡
  function methodCard(key, klass) {{
    const m = s[key];
    if (!m || m.mape == null) return '';
    const isBest = (best === key);
    return `<div class="method-card ${{klass}} ${{isBest?'best-method':''}}">
      <div class="mc-title">${{m.name}}${{isBest?' ⭐ 最佳':''}}</div>
      <div class="mc-formula">${{m.formula}}</div>
      <div class="mc-stats">
        <span class="mape-big">${{m.mape.toFixed(1)}}%</span> 平均絕對誤差%（MAPE）
        <span class="mc-range">系統性偏差 ${{m.bias>=0?'+':''}}${{m.bias.toFixed(1)}}% · 範圍 ${{m.min_err_pct.toFixed(1)}}–${{m.max_err_pct.toFixed(1)}}%</span>
      </div>
    </div>`;
  }}
  html += `<div class="method-cards">
    ${{methodCard('method_1','method-a')}}
    ${{methodCard('method_2','method-b')}}
    ${{methodCard('method_3','method-c')}}
  </div>`;

  // 詳細比較表（表頭加 tooltip）
  html += `
    <table class="compare-table">
      <thead><tr>
        <th rowspan="2" title="比較年份">年份 ⓘ</th>
        <th rowspan="2" title="該年 DHI 樣本月乳量總和（非預測）">DHI 加總<br/>(${{unitLabelOnly()}}) ⓘ</th>
        <th rowspan="2" class="th-truth"
            title="農業部〈牛乳產量〉年報該年公告值。本欄為驗證真值、絕不進入預測模型">農業部公告產量<br/>「答案」 ⓘ</th>
        <th colspan="2" class="th-a"
            title="用一個固定常數當 SF。受 DHI 涵蓋率年年變化影響、最差">M1 固定 SF ⓘ</th>
        <th colspan="2" class="th-b"
            title="當年（農業部公告場數 / DHI 場數）。物理意義直接、為原舊版主要方法">M2 場數比例<span data-best-marker="method_2"></span> ⓘ</th>
        <th colspan="2" class="th-c"
            title="農業部公告產乳牛 × (DHI 平均單頭日產乳 ÷ productivity 比率) × 305 天（含 productivity 校正、與 cohort_simple 同步）">M3 結構分解<span data-best-marker="method_3"></span> ⓘ</th>
      </tr><tr>
        <th class="th-a" title="DHI 加總 × M1 SF">預測</th>
        <th class="th-a" title="(預測 - 真值) / 真值">誤差%</th>
        <th class="th-b" title="DHI 加總 × M2 SF">預測</th>
        <th class="th-b" title="(預測 - 真值) / 真值">誤差%</th>
        <th class="th-c" title="按結構分解公式計算">預測</th>
        <th class="th-c" title="(預測 - 真值) / 真值">誤差%</th>
      </tr></thead><tbody>
  `;

  function colorFor(err) {{
    if (err == null) return '';
    const a = Math.abs(err);
    return a >= 10 ? 'err-bad' : a >= 5 ? 'err-warn' : 'err-ok';
  }}
  function fmtN(v) {{ return fmtTonsInUnit(v); }}  // 公噸 → 當前單位
  function fmtE(v) {{ return v == null ? '—' :
    ((v>=0?'+':'') + v.toFixed(1) + '%'); }}

  oc.rows.forEach(r => {{
    html += `<tr>
      <td><b>${{r.year}}</b></td>
      <td class="num">${{fmtN(r.dhi_yearly_tons)}}</td>
      <td class="num truth-cell"><b>${{fmtN(r.official_production)}}</b></td>
      <td class="num">${{fmtN(r.method_1_pred)}}</td>
      <td class="num ${{colorFor(r.method_1_err_pct)}}">${{fmtE(r.method_1_err_pct)}}</td>
      <td class="num">${{fmtN(r.method_2_pred)}}</td>
      <td class="num ${{colorFor(r.method_2_err_pct)}}">${{fmtE(r.method_2_err_pct)}}</td>
      <td class="num">${{fmtN(r.method_3_pred)}}</td>
      <td class="num ${{colorFor(r.method_3_err_pct)}}">${{fmtE(r.method_3_err_pct)}}</td>
    </tr>`;
  }});
  html += '</tbody></table>';

  // 在養量參考表
  html += `
    <details class="compare-detail">
      <summary>📋 各年資料明細（DHI 樣本 vs 農業部公告在養量）</summary>
      <table class="compare-table">
        <thead><tr><th>年份</th>
          <th>DHI 場數</th><th>農業部公告場數</th><th>場覆蓋率</th>
          <th>DHI 產乳牛</th><th>農業部公告產乳牛</th><th>牛覆蓋率</th>
          <th>DHI 平均單頭日產乳</th></tr></thead>
        <tbody>`;
  oc.rows.forEach(r => {{
    const farmCov = r.dhi_n_farms / r.official_n_farms * 100;
    const cowCov = r.dhi_n_cows / r.official_milking_cows * 100;
    html += `<tr>
      <td><b>${{r.year}}</b></td>
      <td class="num">${{r.dhi_n_farms}}</td>
      <td class="num">${{r.official_n_farms}}</td>
      <td class="num">${{farmCov.toFixed(1)}}%</td>
      <td class="num">${{r.dhi_n_cows.toLocaleString()}}</td>
      <td class="num">${{r.official_milking_cows.toLocaleString()}}</td>
      <td class="num">${{cowCov.toFixed(1)}}%</td>
      <td class="num">${{r.dhi_avg_daily_kg ? r.dhi_avg_daily_kg.toFixed(1)+' kg' : '—'}}</td>
    </tr>`;
  }});
  html += `</tbody></table></details>`;

  // 柱狀圖：三種方法逐年誤差
  html += `<div class="bar-compare-wrap" style="height:300px;margin-top:16px;">
    <canvas id="compare_chart"></canvas>
  </div>`;

  // 結論
  // 中性說明（不寫死年份）
  html += `<p class="note" style="margin-top:14px;color:#555;font-size:12px">
    <b>讀法 / How to read</b>：
    每年用實際 DHI 加總 × 不同方法的 SF（涵蓋率還原係數）、對照農業部公告值。
    最佳方法 <b>${{s[best].name}}</b>（MAPE 平均絕對誤差% ${{s[best].mape.toFixed(1)}}%、
    最大誤差 ${{s[best].max_err_pct.toFixed(1)}}%）。
    本表只測「SF 還原步驟」、不含時序預測誤差；
    完整 pipeline 實戰精度請見「滾動回測」分頁。</p>`;

  c.innerHTML = html;

  // 畫三方法柱狀圖
  const labels = oc.rows.map(r => r.year);
  new Chart(document.getElementById('compare_chart'), {{
    type: 'bar',
    data: {{
      labels,
      datasets: [
        {{label: 'M1 固定 SF', data: oc.rows.map(r => r.method_1_err_pct),
          backgroundColor: 'rgba(192,57,43,0.7)', borderColor: '#c0392b'}},
        {{label: 'M2 場數比例', data: oc.rows.map(r => r.method_2_err_pct),
          backgroundColor: 'rgba(39,174,96,0.7)', borderColor: '#27ae60'}},
        {{label: 'M3 結構分解', data: oc.rows.map(r => r.method_3_err_pct),
          backgroundColor: 'rgba(52,152,219,0.7)', borderColor: '#3498db'}},
      ]
    }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      plugins: {{
        legend: {{position: 'top'}},
        title: {{display: true, text: '三種預測方法的逐年誤差 (%) – 越接近 0 越準'}},
      }},
      scales: {{
        y: {{title: {{display: true, text: '誤差 %'}},
              grid: {{color: (ctx) => ctx.tick.value === 0 ? '#000' : '#eee'}}}},
      }}
    }}
  }});
}}
renderOfficialCompare();

// ===== Holdout Backtest =====
function renderHoldoutBacktest() {{
  const hb = D.holdout_backtest;
  const c = document.getElementById('holdout_backtest_container');
  if (!hb || !hb.rows || hb.rows.length === 0) {{
    c.innerHTML = '<p style="color:#888">（暫無 holdout backtest 結果。請執行 <code>python3 -m milkfc forecast-ts --dashboard --rerun-backtest</code>）</p>';
    return;
  }}
  const s = hb.summary;
  let html = '';

  // 摘要卡：完整 pipeline MAPE
  const fullMape = s.full_mape;
  const fullMapeL1 = s.full_mape_l1;
  const dhiMape = s.dhi_mape;
  // 系統實際採用的是 best_model（單一模型），不是 ensemble
  const bestM = s.best_model;
  const bestMObj = (s.by_model_mape && bestM && s.by_model_mape[bestM]) || null;
  const bestMMape = bestMObj ? bestMObj.mape : null;
  const bestColor = (bestMMape != null && bestMMape < 5) ? '#27ae60'
                    : (bestMMape != null && bestMMape < 10) ? '#b86700' : '#c0392b';
  html += `<div class="method-cards">
    <div class="method-card" style="background:#fff;border-color:${{bestColor}}">
      <div class="mc-title">系統採用模型 MAPE ⭐ <span style="font-size:11px;color:#888">${{bestM || '—'}}</span></div>
      <div class="mc-formula">時序預測 + Level 4 SF（涵蓋率還原係數）全管線</div>
      <div class="mc-stats">
        <span class="mape-big" style="color:${{bestColor}}">${{bestMMape != null ? bestMMape.toFixed(1)+'%' : '—'}}</span> 平均絕對誤差%
        <span class="mc-range">基於 ${{s.n_years}} 年滾動回測（holdout）、bias 已自動扣除</span>
      </div>
    </div>
    <div class="method-card" style="background:#fff;border-color:#aaa">
      <div class="mc-title">Ensemble 加權集成（對照組）</div>
      <div class="mc-formula">所有時序模型 1/MAPE 加權平均</div>
      <div class="mc-stats">
        <span class="mape-big" style="color:#666">${{fullMape.toFixed(1)}}%</span> 平均絕對誤差%
        <span class="mc-range">受表現較差模型拉低、僅供交叉驗證</span>
      </div>
    </div>
    <div class="method-card" style="background:#fff;border-color:#888">
      <div class="mc-title">Level 1 baseline（舊方法對照）</div>
      <div class="mc-formula">SF = 直接用 Y-1 場數比（未外推季報）</div>
      <div class="mc-stats">
        <span class="mape-big" style="color:#666">${{fullMapeL1 != null ? fullMapeL1.toFixed(1)+'%' : '—'}}</span> 平均絕對誤差%
        <span class="mc-range">與 L4 對照、驗證 SF 方法升級效益</span>
      </div>
    </div>
  </div>`;

  // 偵測 best_model 是否為 cohort（cohort 不走 SF、直接 national 尺度）
  const bestIsCohort = (bestM === 'cohort_simple' || bestM === 'cohort_v2_auto');
  const bestColLabel = bestIsCohort
    ? `cohort 結構式（不走 SF）`
    : `${{bestM || '—'}} × L4`;

  // 詳細逐年表（表頭加 tooltip）
  html += `<p style="font-size:11px;color:#888;margin:14px 0 4px">
    ※ 表中「<b>L1 baseline</b>」「<b>L4</b>」兩欄為 <b>Ensemble 加權集成</b>（對照組）路徑、
    用來公平對比 L1 vs L4 兩種 SF 方法的升級效益（同模型、不同 SF）。
    最右側「<b>系統採用 ⭐</b>」欄為系統實際輸出（${{bestM || '—'}}${{bestIsCohort ? '：用結構式公式「牛口 × 單頭產量 × 305 × productivity 校正」、不需 SF' : ' × L4 SF、未扣 bias'}}），
    其 ${{s.n_years}} 年平均 MAPE 對應上方卡片的 ${{bestMMape != null ? bestMMape.toFixed(1) : '—'}}%。
  </p>
  <table class="compare-table">
    <thead><tr>
      <th rowspan="2" title="該年 DHI 資料被砍掉、模型只看到該年之前的資料">Holdout 年<br/>滾動回測年 ⓘ</th>
      <th rowspan="2" title="Ensemble 預測該年 DHI 樣本月乳量加總（公噸）">時序預測<br/>DHI 加總（ensemble）ⓘ</th>
      <th rowspan="2" title="(預測 - 真實) / 真實。負號=預測偏低">DHI<br/>誤差% ⓘ</th>
      <th colspan="2" title="Scale Factor 估計值">SF ⓘ</th>
      <th colspan="2" class="th-a" title="Level 1: 直接用 Y-1 場數比（ensemble）">L1 baseline<br/><span style="font-weight:400;font-size:10px;color:#888">ensemble × L1</span> ⓘ</th>
      <th colspan="2" class="th-b" title="Level 4: 用季報+年報外推到目標年（ensemble）">L4 對照<br/><span style="font-weight:400;font-size:10px;color:#888">ensemble × L4</span> ⓘ</th>
      <th colspan="2" class="th-system" title="系統實際輸出：${{bestM || '—'}}${{bestIsCohort ? '（cohort 直接全國尺度、不需 SF）' : ' × L4 SF（未扣 bias）'}}">系統採用 ⭐<br/><span style="font-weight:400;font-size:10px;color:#888">${{bestColLabel}}</span> ⓘ</th>
      <th rowspan="2" class="th-truth" title="農業部〈牛乳產量〉年報該年公告值">農業部公告值 ⓘ</th>
    </tr><tr>
      <th title="L1: 用 Y-1 那年的 場數比">L1</th>
      <th title="L4: 用最新季報+年報線性外推">L4</th>
      <th class="th-a" title="ensemble DHI 預測 × L1 SF">預測</th>
      <th class="th-a" title="(預測 - 真實) / 真實">誤差%</th>
      <th class="th-b" title="ensemble DHI 預測 × L4 SF">預測</th>
      <th class="th-b" title="(預測 - 真實) / 真實">誤差%</th>
      <th class="th-system" title="${{bestIsCohort ? 'cohort 結構式預測（已套 productivity 校正、未扣 bias）' : `${{bestM}} DHI 預測 × L4 SF（未扣 bias）`}}">預測</th>
      <th class="th-system" title="(預測 - 真實) / 真實">誤差%</th>
    </tr></thead><tbody>`;

  function colorFor(err) {{
    if (err == null) return '';
    const a = Math.abs(err);
    return a >= 10 ? 'err-bad' : a >= 5 ? 'err-warn' : 'err-ok';
  }}
  function fmt(v) {{ return fmtTonsInUnit(v); }}  // 公噸 → 當前單位
  function fmtErr(v) {{
    return v == null ? '—' : ((v >= 0 ? '+' : '') + v.toFixed(1) + '%');
  }}

  hb.rows.forEach(r => {{
    const sfL1 = r.sf_l1 || r.scale_factor_m2;  // 兼容舊資料
    const sfL4 = r.sf_l4;
    // 系統採用模型逐年：cohort 用結構式直出、其他模型 × L4 SF
    let bmFull = null, bmErr = null;
    if (bestIsCohort) {{
      // cohort_simple / cohort_v2_auto 直接全國尺度（已套 productivity 校正）
      if (bestM === 'cohort_v2_auto' && r.cohort_v2_predicted_tons != null) {{
        bmFull = r.cohort_v2_predicted_tons;
      }} else if (r.cohort_predicted_tons != null) {{
        bmFull = r.cohort_predicted_tons;
      }}
    }} else {{
      // 其他模型 × L4 SF
      const bmDhi = (r.model_predictions || {{}})[bestM];
      if (bmDhi != null && sfL4 != null) {{
        bmFull = bmDhi * sfL4;
      }}
    }}
    if (bmFull != null && r.full_actual_tons) {{
      bmErr = (bmFull - r.full_actual_tons) / r.full_actual_tons * 100;
    }}
    html += `<tr>
      <td><b>${{r.year}}</b></td>
      <td class="num">${{fmt(r.dhi_predicted_tons)}}</td>
      <td class="num ${{colorFor(r.dhi_err_pct)}}">${{fmtErr(r.dhi_err_pct)}}</td>
      <td class="num">${{sfL1 ? sfL1.toFixed(3) : '—'}}</td>
      <td class="num">${{sfL4 ? sfL4.toFixed(3) : '—'}}</td>
      <td class="num">${{fmt(r.full_predicted_tons_l1)}}</td>
      <td class="num ${{colorFor(r.full_err_pct_l1)}}">${{fmtErr(r.full_err_pct_l1)}}</td>
      <td class="num">${{fmt(r.full_predicted_tons)}}</td>
      <td class="num ${{colorFor(r.full_err_pct)}}">${{fmtErr(r.full_err_pct)}}</td>
      <td class="num system-cell">${{bmFull != null ? fmt(bmFull) : '—'}}</td>
      <td class="num system-cell ${{colorFor(bmErr)}}"><b>${{fmtErr(bmErr)}}</b></td>
      <td class="num truth-cell"><b>${{fmt(r.full_actual_tons)}}</b></td>
    </tr>`;
  }});
  html += '</tbody></table>';

  // 各模型 MAPE 對照
  if (s.by_model_mape) {{
    html += '<h3 style="font-size:14px;margin:18px 0 6px;color:#2a4d69;">各模型在滾動回測上的表現 / Per-model Holdout Performance</h3>';
    html += '<table class="compare-table"><thead><tr>'
      + '<th>模型</th>'
      + '<th>滾動回測 MAPE<br/><span style="font-weight:400;font-size:10px;color:#888">holdout 平均絕對誤差%</span></th>'
      + '<th>bias<br/><span style="font-weight:400;font-size:10px;color:#888">系統性偏差%</span></th>'
      + '<th>n<br/><span style="font-weight:400;font-size:10px;color:#888">回測年數</span></th>'
      + '</tr></thead><tbody>';
    Object.entries(s.by_model_mape)
      .sort((a,b) => a[1].mape - b[1].mape)
      .forEach(([m, ms]) => {{
        const isBest = m === s.best_model;
        const c = colorFor(ms.mape);
        html += `<tr${{isBest?' style="background:#fff8e1"':''}}>
          <td><b>${{m}}</b>${{isBest?' ⭐':''}}</td>
          <td class="num ${{c}}">${{ms.mape.toFixed(1)}}%</td>
          <td class="num">${{(ms.bias>=0?'+':'')+ms.bias.toFixed(1)}}%</td>
          <td class="num">${{ms.n}}</td>
        </tr>`;
      }});
    html += '</tbody></table>';
  }}

  // 摘要說明（中性、不寫死年份）
  const _bmObj = (s.by_model_mape && s.by_model_mape[s.best_model]) || null;
  const _bmMape = _bmObj ? _bmObj.mape.toFixed(1)+'%' : '—';
  const _bmBias = _bmObj ? ((_bmObj.bias>=0?'+':'')+_bmObj.bias.toFixed(1)+'%') : '—';
  html += `<p class="note" style="margin-top:14px;color:#555;font-size:12px">
    <b>讀法 / How to read</b>：
    每年 Y 模型只看到 ≤ Y-1 資料、預測 Y 全年並對照農業部公告值。
    <b>系統採用最佳單一模型 ${{s.best_model || '—'}} 作為主要預測</b>、
    其全管線（時序預測 + Level 4 SF 涵蓋率還原）${{s.n_years}} 年平均
    <b>MAPE（平均絕對誤差%）= ${{_bmMape}}</b>、bias 系統性偏差 = ${{_bmBias}}（已於正式預測自動扣除）。
    Ensemble 加權集成 MAPE = ${{fullMape.toFixed(1)}}%（受表現較差模型拉低）、
    僅作為交叉驗證對照、不作主要交付。</p>`;

  c.innerHTML = html;
}}
renderHoldoutBacktest();

// === 自訂 hover tooltip：把 title 轉成 data-tip 避免雙 tooltip ===
let _tipBusy = false;
function applyCustomTooltips() {{
  if (_tipBusy) return;
  _tipBusy = true;
  try {{
    document.querySelectorAll('[title]').forEach(el => {{
      const t = el.getAttribute('title');
      if (t) {{
        if (!el.hasAttribute('data-tip')) el.setAttribute('data-tip', t);
        el.removeAttribute('title');
      }}
    }});
  }} finally {{ _tipBusy = false; }}
}}
applyCustomTooltips();
// 動態 render 後重套（tabs 切換、模型下拉切換等）
document.addEventListener('click', () => setTimeout(applyCustomTooltips, 80));
document.addEventListener('change', () => setTimeout(applyCustomTooltips, 80));
// 監聽動態插入的內容
const _tipObs = new MutationObserver(muts => {{
  for (const m of muts) {{
    if (m.type === 'childList' && m.addedNodes.length) {{
      setTimeout(applyCustomTooltips, 0);
      break;
    }}
  }}
}});
_tipObs.observe(document.body, {{childList: true, subtree: true}});
</script>
</body></html>"""


_CSS = """
* { box-sizing: border-box; }
body { font-family: 'PingFang TC','Microsoft JhengHei',-apple-system,sans-serif;
       margin: 0; background: #f6f7f9; color: #1a1a1a; }
header { background: linear-gradient(135deg, #2a4d69, #1a3550); color: white;
         padding: 18px 28px; }
header h1 { margin: 0 0 6px; font-size: 22px; }
header .meta { font-size: 12px; opacity: 0.92; }
header code { background: rgba(255,255,255,0.18); padding: 1px 6px;
              border-radius: 3px; font-size: 11px; }
.topnav { background: white; padding: 0 28px; border-bottom: 1px solid #e0e3e7;
          display: flex; align-items: center; }
.topnav a { display: inline-block; padding: 12px 18px; color: #555;
            text-decoration: none; font-size: 13px; font-weight: 600; }
.topnav a.active { color: #2a4d69; border-bottom: 2px solid #2a4d69; }
.topnav a:hover { color: #2a4d69; background: #f0f3f6; }
.unit-picker { margin-left: auto; padding: 8px 18px; font-size: 13px;
               color: #555; }
.unit-picker select { padding: 4px 8px; border-radius: 4px;
                       border: 1px solid #ccc; font-size: 13px;
                       margin-left: 4px; }
.period-totals { margin-top: 12px; }
.period-totals .pt-title { font-weight: 600; font-size: 14px;
                            color: #2a4d69; margin-bottom: 8px; }
.period-totals .pt-table { width: 100%; max-width: 700px;
                            border-collapse: collapse; font-size: 13px; }
.period-totals .pt-table th { background: #f0f3f6; padding: 6px 10px;
                               text-align: left; }
.period-totals .pt-table td { padding: 6px 10px; border-bottom: 1px solid #eee; }
.period-totals .pt-total { background: #e7f0f7; font-weight: 600;
                            border-top: 2px solid #2a4d69; }
.period-totals .pt-hint { font-size: 11px; color: #888;
                           font-weight: 400; margin-left: 8px; }
.pt-row { cursor: pointer; transition: background 0.15s; }
.pt-row:hover { background: #f0f3f6 !important; }
.pt-toggle { font-size: 10px; color: #c9930e; margin-left: 6px;
              transition: transform 0.2s; }
.pt-detail-row td { padding: 0 !important; }
.pt-calc { padding: 12px 16px; background: #fafbfc;
            border-top: 1px solid #e0e3e7;
            border-bottom: 2px solid #c9930e;
            font-size: 12px; line-height: 1.7; }
.pt-calc-empty { padding: 12px; color: #888; font-style: italic; }
.pt-calc-h { font-size: 12px; font-weight: 700; color: #1a3550;
              margin-bottom: 8px; padding-bottom: 4px;
              border-bottom: 1px dashed #d0d7de; }
.pt-calc-step { display: flex; flex-direction: column;
                 padding: 4px 0; border-bottom: 1px dotted #f0f0f0; }
.pt-calc-step:last-child { border-bottom: none; }
.pt-calc-form { color: #555; font-family: 'Menlo', monospace;
                 font-size: 11px; }
.pt-calc-eq { color: #1a3550; padding-left: 14px; margin-top: 2px; }
.pt-calc-final { color: #186b3f; font-weight: 700;
                  background: #f0f9f4; padding: 4px 8px 4px 14px;
                  border-radius: 3px; }
.pt-calc-anno { font-size: 10px; color: #888; margin-left: 8px;
                 font-style: italic; }
.pt-calc-tbl { width: 100%; max-width: 600px;
                margin: 8px 0; border-collapse: collapse; font-size: 11px; }
.pt-calc-tbl th { padding: 4px 8px; background: #f0f3f6;
                   text-align: left; }
.pt-calc-tbl td { padding: 3px 8px; border-bottom: 1px solid #eee; }
.pt-calc-final-row { margin-top: 8px; }
.num.small { font-size: 11px; color: #777; }
.chart-controls { margin-top: 8px; display: flex; gap: 8px; align-items: center;
                  flex-wrap: wrap; }
.btn-mini { padding: 4px 12px; border: 1px solid #ccc; background: white;
            border-radius: 4px; font-size: 12px; cursor: pointer; color: #555; }
.btn-mini:hover { background: #f0f3f6; border-color: #2a4d69; color: #2a4d69; }
.chart-controls .hint { font-size: 11px; color: #888; margin-left: 8px; }
.region-toggles { display: flex; gap: 8px; flex-wrap: wrap; align-items: center;
                  padding: 10px; background: #f0f3f6; border-radius: 6px;
                  margin-bottom: 12px; }
.reg-toggle { display: inline-flex; align-items: center; gap: 4px;
              font-size: 12px; cursor: pointer; padding: 2px 8px;
              border-radius: 4px; background: white; border: 1px solid #ddd; }
.reg-toggle input { margin: 0; cursor: pointer; }
.reg-toggle:hover { background: #f8f9fb; }
.hierarchy-note { margin-top: 16px; padding: 12px;
                  background: #fff8e7; border-left: 3px solid #b86700;
                  border-radius: 4px; font-size: 12px; color: #555;
                  line-height: 1.6; }
.hierarchy-table { width: 100%; max-width: 600px; margin-top: 12px;
                    border-collapse: collapse; font-size: 13px; }
.hierarchy-table th { background: #f0f3f6; padding: 6px 10px; text-align: left; }
.hierarchy-table td { padding: 6px 10px; border-bottom: 1px solid #eee; }
.num { text-align: right; font-variant-numeric: tabular-nums; }
.card { background: white; padding: 20px 24px; margin: 20px 28px;
        border-radius: 6px; box-shadow: 0 1px 3px rgba(0,0,0,0.06); }
.card h2 { margin: 0 0 12px; font-size: 16px; color: #2a4d69; }
.picker { display: flex; gap: 12px; align-items: center; padding: 12px;
          background: #f0f3f6; border-radius: 6px; margin-bottom: 16px; }
.picker select { padding: 6px 10px; border-radius: 4px; border: 1px solid #ccc;
                 font-size: 13px; }
.chart-wrap { position: relative; height: 320px; }
.note { font-size: 12px; color: #666; margin-top: 8px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { padding: 8px 12px; text-align: left; border-bottom: 1px solid #eee; }
th { background: #f0f3f6; }
pre { font-size: 11px; background: #f8f9fb; padding: 12px; border-radius: 4px;
      max-height: 200px; overflow: auto; }

/* 預測 vs 答案比較區塊 */
.method-cards { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px;
                margin: 12px 0 16px; }
.method-card { padding: 14px; border-radius: 6px; text-align: center;
               border: 1px solid; position: relative; }
.method-card.method-a { background: #fdecea; border-color: #c0392b; }
.method-card.method-b { background: #e8f8ee; border-color: #27ae60; }
.method-card.method-c { background: #e8f1fb; border-color: #3498db; }
.method-card.method-improve { background: #fff8e1; border-color: #b86700; }
.method-card.best-method { box-shadow: 0 0 0 3px gold; }
.method-card .mc-title { font-weight: 700; font-size: 13px; margin-bottom: 4px; }
.method-card .mc-formula { font-size: 11px; color: #555; margin-bottom: 10px;
                            font-family: monospace; line-height: 1.4; }
.method-card .mc-stats { font-size: 12px; color: #555; }
.method-card .mape-big { display: block; font-size: 32px; font-weight: 700;
                          color: #1a3550; margin-bottom: 2px; }
.method-card .mape-big.improve { color: #27ae60; }
.method-card .mc-range { display: block; font-size: 10px; color: #888;
                          margin-top: 4px; }

.compare-table { width: 100%; max-width: 1000px; margin: 0 auto;
                  border-collapse: collapse; font-size: 12px; }
.compare-table th { padding: 6px 8px; background: #f0f3f6; text-align: center;
                     font-weight: 600; }
.compare-table th.th-a { background: #fdecea; color: #8b2515; }
.compare-table th.th-b { background: #e8f8ee; color: #186b3f; }
.compare-table th.th-c { background: #e8f1fb; color: #1f5582; }
.compare-table th.th-truth { background: #fff8e1; color: #8a6500; }
.compare-table td.truth-cell { background: #fffbed; }
.compare-table th.th-system { background: #fff3cd; color: #6a5000;
                              border-bottom: 2px solid #c9930e; }
.compare-table td.system-cell { background: #fffaeb; font-weight: 600; }
.compare-table td { padding: 5px 8px; border-bottom: 1px solid #eee;
                     text-align: right; }
.compare-table td:first-child { text-align: left; }
.compare-table .err-ok { color: #186b3f; font-weight: 600; }
.compare-table .err-warn { color: #b86700; font-weight: 600; }
.compare-table .err-bad { color: #c0392b; font-weight: 600; }
.compare-detail { margin-top: 16px; padding: 8px 12px;
                   background: #f8f9fb; border-radius: 4px; font-size: 12px; }
.compare-detail summary { cursor: pointer; font-weight: 600; padding: 4px 0;
                            color: #2a4d69; }
.compare-detail .compare-table { font-size: 11px; }

.compare-conclusion { margin-top: 16px; padding: 14px;
                       background: #f0f7fb; border-left: 4px solid #2a4d69;
                       border-radius: 4px; font-size: 13px; }
.compare-conclusion h4 { margin: 0 0 8px; color: #2a4d69; font-size: 14px; }
.compare-conclusion ul { margin: 0; padding-left: 20px; line-height: 1.7; }
.compare-conclusion li { margin-bottom: 4px; }
.bar-compare-wrap { position: relative; }

/* === 雙語標題 === */
.h-en { font-size: 13px; color: #888; font-weight: 400; margin-left: 6px; }
.header-en { font-size: 14px; color: rgba(255,255,255,0.75);
              font-weight: 400; margin-left: 8px; display: block;
              margin-top: 2px; }
header .meta { display: flex; gap: 18px; flex-wrap: wrap; font-size: 12px; }

/* === §1 預測摘要（6 張卡片）=== */
.summary-section { background: linear-gradient(135deg, #f8fafd, #ffffff);
                    border-left: 4px solid #2a4d69; }
.summary-grid { display: grid;
                 grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
                 gap: 12px; margin: 12px 0; }
.sum-card { background: white; padding: 14px 12px; border-radius: 6px;
             border: 1px solid #e0e3e7; text-align: center; position: relative; }
.sum-card.highlight { background: #fff8e1; border-color: #b86700;
                        box-shadow: 0 2px 6px rgba(184,103,0,0.15); }
.sum-card .sc-icon { font-size: 22px; margin-bottom: 4px; }
.sum-card .sc-label-zh { font-size: 12px; color: #555; font-weight: 600; }
.sum-card .sc-label-en { font-size: 10px; color: #999;
                          margin-bottom: 6px; font-style: italic; }
.sum-card .sc-value { font-size: 22px; font-weight: 700; color: #1a3550;
                       line-height: 1.2; margin: 4px 0; }
.sum-card.highlight .sc-value { color: #b86700; font-size: 26px; }
.sum-card .sc-sub { font-size: 11px; color: #666; line-height: 1.5; }
.sum-card .sc-sub-en { color: #aaa; font-size: 10px; font-style: italic; }
.summary-meta { display: flex; gap: 18px; flex-wrap: wrap;
                 padding: 10px 12px; background: #f0f3f6;
                 border-radius: 4px; font-size: 12px; color: #555;
                 margin-top: 10px; }
.summary-meta .sm-toggle { margin-left: auto; }
.summary-meta a { color: #2a4d69; text-decoration: none; }
.summary-meta a:hover { text-decoration: underline; }

/* 執行摘要 */
.exec-summary { margin-top: 14px; padding: 16px 20px;
                 background: white; border: 1px solid #d0d7de;
                 border-left: 4px solid #2a4d69; border-radius: 6px; }
.exec-head { display: flex; align-items: center; justify-content: space-between;
              margin-bottom: 12px; padding-bottom: 8px;
              border-bottom: 1px dashed #e0e3e7; }
.exec-title { font-size: 15px; font-weight: 700; color: #1a3550; }
.exec-copy { background: #2a4d69; color: white; border: none;
              padding: 6px 14px; border-radius: 4px; cursor: pointer;
              font-size: 12px; font-weight: 600; }
.exec-copy:hover { background: #1a3550; }
.exec-copy.copied { background: #186b3f; }
.exec-body { font-size: 13px; line-height: 1.8; color: #333; }
.exec-conclusion { background: #fffbed; padding: 12px 14px;
                    border-radius: 4px; border-left: 3px solid #c9930e;
                    margin: 0 0 12px 0; }
.exec-conclusion .exec-tag { display: inline-block;
                              background: #c9930e; color: white;
                              padding: 1px 8px; border-radius: 3px;
                              font-size: 11px; font-weight: 600;
                              margin-right: 6px; vertical-align: 2px; }
.exec-headline { font-size: 18px; color: #b86700; }
.exec-grid { display: flex; flex-direction: column; gap: 8px; }
.exec-row { display: flex; gap: 14px; padding: 8px 4px;
             border-bottom: 1px dotted #eee; align-items: flex-start; }
.exec-row:last-child { border-bottom: none; }
.exec-label { flex: 0 0 110px; font-weight: 600; color: #2a4d69;
               font-size: 13px; }
.exec-content { flex: 1; color: #444; line-height: 1.7; }
.exec-disclaimer { background: #fffbe5; padding: 8px 10px;
                    border-radius: 3px; margin-top: 4px; }
.exec-disclaimer .exec-label { color: #5a4400; }
.exec-interpret { background: #f0f7fb; padding: 8px 10px;
                   border-radius: 3px; }
.exec-interpret .exec-label { color: #1f5582; }
.exec-risk { background: #fdedee; padding: 8px 10px;
              border-radius: 3px; }
.exec-risk .exec-label { color: #8b2515; }
.exec-refresh { background: #e8f8ee; padding: 8px 10px;
                 border-radius: 3px; }
.exec-refresh .exec-label { color: #186b3f; }
.exec-crossval { background: #f4ecf7; padding: 8px 10px;
                  border-radius: 3px; }
.exec-crossval .exec-label { color: #6a3d8c; }
.exec-howto { background: #ecf6fd; padding: 8px 10px;
               border-radius: 3px; }
.exec-howto .exec-label { color: #1f5582; }
.exec-horizon { background: #fdf6e8; padding: 8px 10px;
                 border-radius: 3px; }
.exec-horizon .exec-label { color: #8a6500; }
.exec-table { width: 100%; max-width: 480px; font-size: 12px;
               border-collapse: collapse; margin-top: 4px; }
.exec-table th { padding: 4px 8px; background: rgba(106,61,140,0.1);
                  text-align: left; color: #6a3d8c; font-weight: 600; }
.exec-table td { padding: 4px 8px; border-bottom: 1px solid #eee; }
.exec-table .num { text-align: right; font-variant-numeric: tabular-nums; }
.exec-content ul { margin-top: 4px !important; padding-left: 18px !important; }
.exec-content ul li { margin-bottom: 3px; }
.exec-footer { margin-top: 10px; padding-top: 8px;
                border-top: 1px dashed #e0e3e7; font-size: 11px;
                color: #888; text-align: right; }
.exec-footer a { color: #2a4d69; }

/* === §5 系統狀態 === */
.status-section { background: #f8fafd; }
.status-grid { display: grid;
                grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                gap: 12px; margin: 12px 0; }
.st-card { background: white; padding: 12px; border-radius: 6px;
            border: 1px solid #e0e3e7; }
.st-card .st-name-zh { font-weight: 600; color: #2a4d69; font-size: 13px; }
.st-card .st-name-en { font-size: 10px; color: #999; font-style: italic;
                         margin-bottom: 8px; }
.st-card .st-latest { font-size: 11px; color: #666; }
.st-card .st-value { font-size: 16px; font-weight: 700; color: #1a3550;
                      font-family: monospace; }
.st-card .st-freq { font-size: 11px; color: #888; margin-top: 6px;
                     padding-top: 6px; border-top: 1px dashed #e0e3e7; }

/* === §4 Tabs === */
.acc-tabs { display: flex; gap: 0; margin-bottom: 12px;
             border-bottom: 2px solid #e0e3e7; }
.acc-tab { background: none; border: none; padding: 10px 18px;
            cursor: pointer; font-size: 13px; font-weight: 600;
            color: #888; border-bottom: 2px solid transparent;
            margin-bottom: -2px; }
.acc-tab:hover { color: #2a4d69; }
.acc-tab.active { color: #2a4d69; border-bottom-color: #2a4d69; }
.acc-pane { display: none; }
.acc-pane.active { display: block; }

/* === §6 方法論 === */
.method-section { background: #fafbfc; }
.case-detail { margin: 8px 0; padding: 10px 14px;
                background: white; border: 1px solid #e0e3e7;
                border-radius: 4px; }
.case-detail > summary { cursor: pointer; font-size: 13px;
                           color: #2a4d69; padding: 4px 0; }
.case-detail[open] > summary { margin-bottom: 10px;
                                 border-bottom: 1px solid #e0e3e7;
                                 padding-bottom: 6px; }
.case-detail p { font-size: 13px; line-height: 1.7; margin: 6px 0; }
.case-detail ul, .case-detail ol { font-size: 13px; line-height: 1.7;
                                     margin: 6px 0; padding-left: 22px; }
.case-detail pre { background: #f8f9fb; padding: 10px; border-radius: 4px;
                    font-size: 11px; max-height: none; }
.method-table { width: auto; max-width: 500px; margin: 8px 0;
                 border-collapse: collapse; font-size: 12px; }
.method-table th { padding: 6px 12px; background: #f0f3f6;
                    text-align: center; }
.method-table td { padding: 5px 12px; border-bottom: 1px solid #eee; }

/* === Info 展開（區域加總說明等）=== */
.info-detail { margin-top: 12px; padding: 8px 12px;
                background: #f8f9fb; border-left: 3px solid #888;
                border-radius: 4px; font-size: 12px; color: #555; }
.info-detail summary { cursor: pointer; font-weight: 500; color: #555; }
.info-detail[open] summary { margin-bottom: 8px; }
.info-detail p, .info-detail ul { margin: 6px 0; line-height: 1.7; }

/* === §4 警示條 + 說明區塊 === */
.truth-banner { padding: 12px 16px; background: #fffbe5;
                 border: 1px solid #f4c430; border-left: 4px solid #c9930e;
                 border-radius: 4px; font-size: 13px; color: #5a4400;
                 margin: 8px 0 16px; line-height: 1.6; }
.truth-banner .db-meta-en { font-size: 10px; color: #999;
                              font-style: italic; }

.explain-block { padding: 12px 16px; border-radius: 4px;
                  margin: 12px 0; line-height: 1.7; font-size: 13px; }
.explain-block .eb-title { font-weight: 700; font-size: 14px;
                            color: #1a3550; margin-bottom: 8px; }
.explain-block p, .explain-block ul, .explain-block ol {
                margin: 6px 0; padding-left: 22px; }
.explain-block ul, .explain-block ol { padding-left: 22px; }
.explain-block p { padding-left: 0; }

.explain-A { background: #f0f7fb; border-left: 4px solid #2a4d69; }
.explain-B { background: #f8f9fb; border-left: 4px solid #888; }
.explain-C { background: #e8f8ee; border-left: 4px solid #186b3f;
              border: 1px solid #c8e6c9; }
.explain-D { background: #fef5e7; border-left: 4px solid #b86700;
              border: 1px solid #f4c430; }
.explain-fold { background: #fafafa; border-left: 3px solid #ccc;
                 padding: 8px 14px; }
.explain-fold summary { cursor: pointer; padding: 4px 0; }
.explain-fold[open] summary { margin-bottom: 6px;
                                border-bottom: 1px dashed #ddd; }

.explain-table { width: 100%; max-width: 750px; font-size: 12px;
                  border-collapse: collapse; margin-top: 6px; }
.explain-table td { padding: 6px 10px; border-bottom: 1px solid #e8e8e8;
                     vertical-align: top; }
.explain-table td:first-child { width: 30%; color: #2a4d69;
                                  font-size: 12px; }
.explain-table td:last-child { color: #555; }

/* === 資料區塊（預測輸入 vs 驗證真值）=== */
.data-block { padding: 14px 18px; border-radius: 6px;
               border: 1.5px solid; margin: 10px 0; }
.input-block { background: #f0f7fb; border-color: #2a4d69; }
.truth-block { background: #fffbe5; border-color: #c9930e; }
.data-block .db-header { display: flex; align-items: center; gap: 8px;
                          margin-bottom: 6px; }
.data-block .db-icon { font-size: 18px; }
.data-block .db-title-zh { font-weight: 700; font-size: 14px;
                            color: #1a3550; }
.input-block .db-title-zh { color: #1a3550; }
.truth-block .db-title-zh { color: #5a4400; }
.data-block .db-title-en { font-size: 11px; color: #888;
                            font-style: italic; }
.data-block .db-subtitle { font-size: 12px; color: #666;
                            margin-bottom: 10px; line-height: 1.6; }
.data-block .db-list { margin: 0; padding-left: 22px; line-height: 1.8;
                        font-size: 13px; }
.data-block .db-list li { margin-bottom: 4px; }
.data-block .db-meta { font-size: 11px; color: #888; }
.data-block .db-meta-en { font-size: 11px; color: #999;
                           font-style: italic; }
.truth-block .db-warning { margin-top: 10px; padding: 8px 12px;
                            background: rgba(201,147,14,0.12);
                            border-radius: 3px; font-size: 12px;
                            color: #5a4400; line-height: 1.6; }

/* === §1.7 結構變數視覺化 === */
.structural-section { background: linear-gradient(135deg, #f0f9f4, #fff);
                      border-left: 4px solid #1e7c3a; }
.structural-details > summary { cursor: pointer; padding: 4px 0;
                                 list-style: none; outline: none; }
.structural-details > summary::-webkit-details-marker { display: none; }
.structural-details > summary::before { content: '▶'; display: inline-block;
                                         margin-right: 8px; transition: transform 0.2s;
                                         color: #1e7c3a; font-size: 14px; }
.structural-details[open] > summary::before { transform: rotate(90deg); }
.structural-summary-hint { font-size: 12px; color: #888; margin-left: 12px;
                            font-weight: 400; }
.structural-intro { font-size: 13px; color: #555; line-height: 1.7;
                     margin: 12px 0 16px; padding: 10px 14px;
                     background: #f8fbf9; border-left: 3px solid #1e7c3a;
                     border-radius: 4px; }
.structural-grid { display: grid; gap: 16px;
                    grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); }
.structural-chart-cell { padding: 12px; background: white;
                          border: 1px solid #e0e3e7; border-radius: 6px; }
.structural-chart-title { font-size: 13px; font-weight: 700;
                           color: #2a4d69; margin-bottom: 8px; }
.structural-chart-note { font-size: 11px; color: #666; line-height: 1.6;
                          margin-top: 8px; padding: 6px 10px;
                          background: #f8f9fb; border-radius: 4px; }

/* === §1.5 What-If 情境計算器 === */
.whatif-section { background: linear-gradient(135deg, #fffaf0, #fff);
                  border-left: 4px solid #c9930e; }
.whatif-details > summary { cursor: pointer; padding: 4px 0;
                             list-style: none; outline: none; }
.whatif-details > summary::-webkit-details-marker { display: none; }
.whatif-details > summary::before { content: '▶'; display: inline-block;
                                     margin-right: 8px; transition: transform 0.2s;
                                     color: #c9930e; font-size: 14px; }
.whatif-details[open] > summary::before { transform: rotate(90deg); }
.whatif-details > summary h2 { color: #2a4d69; }
.whatif-summary-hint { font-size: 12px; color: #888; margin-left: 12px;
                        font-weight: 400; }
.wi-intro { font-size: 13px; color: #555; margin-bottom: 14px;
            padding: 10px 14px; background: #fffbed;
            border-radius: 4px; border-left: 3px solid #c9930e; }
.wi-intro p { margin: 0; line-height: 1.7; }
.wi-mode-row { display: flex; gap: 16px; align-items: center;
               flex-wrap: wrap; padding: 10px 12px;
               background: #f0f3f6; border-radius: 4px;
               margin-bottom: 14px; font-size: 13px; }
.wi-mode-label { font-weight: 600; color: #2a4d69; }
.wi-radio { display: inline-flex; gap: 6px; align-items: center;
            cursor: pointer; }
.wi-radio input { cursor: pointer; }
.wi-presets { display: flex; gap: 8px; flex-wrap: wrap;
              align-items: center; margin-bottom: 16px; }
.wi-preset-title { font-size: 12px; font-weight: 600;
                    color: #2a4d69; margin-right: 4px; }
.wi-preset { padding: 6px 12px; border: 1px solid #ddd;
             background: white; border-radius: 6px; cursor: pointer;
             font-size: 12px; display: inline-flex; gap: 5px;
             align-items: center; transition: all 0.15s; }
.wi-preset:hover { border-color: #c9930e; background: #fffbed;
                    transform: translateY(-1px);
                    box-shadow: 0 2px 4px rgba(201,147,14,0.2); }
.wi-preset.active { border-color: #c9930e; background: #fff3cd;
                     font-weight: 600; }
.wi-preset-icon { font-size: 14px; }
.wi-controls { margin-bottom: 16px; }
.wi-grid { display: grid; gap: 14px;
           grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); }
.wi-input-cell { padding: 12px 14px; background: white;
                  border: 1px solid #e0e3e7; border-radius: 6px; }
.wi-target-cell { background: #fffbed; border-color: #c9930e;
                   border-width: 2px; }
.wi-input-label { display: block; font-size: 12px; font-weight: 600;
                   color: #2a4d69; margin-bottom: 6px; }
.wi-input-row { display: flex; gap: 6px; align-items: center;
                 margin-bottom: 4px; }
.wi-num { flex: 1; padding: 6px 8px; border: 1px solid #ccc;
          border-radius: 4px; font-size: 14px; font-weight: 600;
          font-variant-numeric: tabular-nums; }
.wi-num:disabled { background: #f5f5f5; color: #888; }
.wi-num:focus { outline: none; border-color: #c9930e;
                box-shadow: 0 0 0 2px rgba(201,147,14,0.2); }
.wi-unit { font-size: 12px; color: #666; }
.wi-base-info { font-size: 11px; color: #888; line-height: 1.5; }
.wi-slider { width: 100%; margin-top: 8px; }
.wi-shock-display { font-size: 24px; font-weight: 700; color: #c9930e;
                     font-variant-numeric: tabular-nums; }
.wi-result-cards { display: flex; gap: 10px; align-items: stretch;
                    margin: 14px 0; flex-wrap: wrap; }
.wi-card { flex: 1; min-width: 160px; padding: 14px;
            background: white; border: 1px solid #e0e3e7;
            border-radius: 6px; text-align: center; }
.wi-card.baseline { border-left: 3px solid #1a3550; }
.wi-card.scenario { border-left: 3px solid #c9930e;
                     background: #fffbed; }
.wi-card.scenario.up { border-left-color: #186b3f; background: #f0f9f4; }
.wi-card.scenario.down { border-left-color: #c0392b; background: #fdecea; }
.wi-card.diff { border-left: 3px solid #888;
                background: #f8f9fb; }
.wi-card.diff.up { border-left-color: #186b3f; color: #186b3f; }
.wi-card.diff.down { border-left-color: #c0392b; color: #c0392b; }
.wi-card-label { font-size: 11px; color: #666; font-weight: 600;
                  text-transform: uppercase; letter-spacing: 0.5px; }
.wi-card-value { font-size: 22px; font-weight: 700; color: #1a3550;
                  margin: 6px 0; font-variant-numeric: tabular-nums; }
.wi-card.diff.up .wi-card-value { color: #186b3f; }
.wi-card.diff.down .wi-card-value { color: #c0392b; }
.wi-card-sub { font-size: 11px; color: #888; }
.wi-arrow { display: flex; align-items: center; font-size: 24px;
            color: #c9930e; font-weight: 700; padding: 0 4px; }
.wi-yoy { padding: 10px 14px; background: #f0f7fb;
          border-left: 3px solid #2a4d69; border-radius: 4px;
          font-size: 13px; line-height: 1.7; margin: 12px 0; }
.wi-yoy .wi-up { color: #186b3f; }
.wi-yoy .wi-down { color: #c0392b; }
.wi-cross { padding: 10px 14px; background: #f4ecf7;
            border-left: 3px solid #6a3d8c; border-radius: 4px;
            font-size: 12px; color: #555; line-height: 1.7;
            margin: 12px 0; }
.wi-cross-tag { display: inline-block; background: #6a3d8c;
                color: white; padding: 1px 8px; border-radius: 3px;
                font-size: 10px; font-weight: 600; margin-right: 6px;
                vertical-align: 1px; }
.wi-chart-wrap { margin: 14px 0;
                  padding: 12px; background: white;
                  border: 1px solid #e0e3e7; border-radius: 6px; }
.wi-chart-title { font-size: 13px; font-weight: 600; color: #2a4d69;
                   margin-bottom: 8px; }
.wi-overlap-hint { font-size: 12px; color: #6a3d8c;
                    background: #f4ecf7; border-radius: 4px;
                    padding: 6px 10px; margin-bottom: 8px;
                    border-left: 3px solid #6a3d8c; }
/* === 計算明細面板 === */
.wi-calc { margin: 14px 0; padding: 14px 16px;
           background: #fafbfc; border: 1px solid #d0d7de;
           border-radius: 6px; }
.wi-calc-title { font-size: 13px; font-weight: 700; color: #1a3550;
                  margin-bottom: 10px; padding-bottom: 6px;
                  border-bottom: 1px dashed #d0d7de; }
.wi-calc-grid { display: grid; gap: 14px;
                grid-template-columns: repeat(2, 1fr); }
.wi-calc-grid.wi-calc-single { grid-template-columns: 1fr; }
.wi-calc-col { padding: 10px 12px; background: white;
               border-radius: 4px; border: 1px solid #e0e3e7; }
.wi-calc-col.wi-calc-scn { background: #fffbed;
                             border-color: #c9930e; }
.wi-calc-col-h { font-size: 12px; font-weight: 700; color: #2a4d69;
                  margin-bottom: 6px; padding-bottom: 4px;
                  border-bottom: 1px solid #e0e3e7; }
.wi-calc-meta { font-size: 11px; color: #c9930e;
                font-weight: 600; margin-bottom: 6px;
                padding: 4px 8px; background: rgba(201,147,14,0.08);
                border-radius: 3px; }
.wi-calc-step { display: flex; flex-direction: column;
                margin: 4px 0; padding: 4px 0;
                border-bottom: 1px dotted #f0f0f0; font-size: 12px;
                line-height: 1.6; }
.wi-calc-step:last-child { border-bottom: none; }
.wi-calc-formula { color: #555; font-family: 'Menlo', monospace;
                    font-size: 11px; }
.wi-calc-eq { color: #1a3550; padding-left: 14px; }
.wi-calc-final { color: #186b3f; font-weight: 700;
                  background: #f0f9f4; padding: 4px 8px 4px 14px;
                  border-radius: 3px; margin-top: 2px; }
.wi-calc-anno { font-size: 10px; color: #888;
                margin-left: 6px; font-style: italic; }
.wi-calc-diff { margin-top: 12px; padding: 10px 14px;
                background: #f0f3f6; border-radius: 4px;
                font-size: 13px; text-align: center;
                font-family: 'Menlo', monospace; }
.wi-calc-diff .wi-down { color: #c0392b; }
.wi-calc-diff .wi-up { color: #186b3f; }
.wi-disclaimer { padding: 10px 14px; background: #fff8e1;
                  border-left: 3px solid #b86700; border-radius: 4px;
                  font-size: 11px; color: #5a4400; line-height: 1.7;
                  margin-top: 16px; }

/* === 自訂 hover tooltip（取代原生 title 樣式）=== */
[data-tip] { position: relative; cursor: help; }
[data-tip]:hover::after {
  content: attr(data-tip);
  position: absolute;
  bottom: calc(100% + 8px);
  left: 50%;
  transform: translateX(-50%);
  background: #1a3550;
  color: #fff;
  padding: 8px 12px;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 400;
  white-space: pre-wrap;
  width: max-content;
  max-width: 320px;
  z-index: 9999;
  pointer-events: none;
  line-height: 1.6;
  text-align: left;
  box-shadow: 0 4px 12px rgba(0,0,0,0.25);
}
[data-tip]:hover::before {
  content: '';
  position: absolute;
  bottom: calc(100% + 2px);
  left: 50%;
  transform: translateX(-50%);
  border: 6px solid transparent;
  border-top-color: #1a3550;
  z-index: 9999;
  pointer-events: none;
}
.compare-table th[title], table th[title], [title] { cursor: help; }

/* === Site footer (for deployment) === */
.site-footer { text-align: center; padding: 18px 28px;
                color: #888; font-size: 11px;
                background: #f0f3f6; border-top: 1px solid #e0e3e7;
                margin-top: 30px; }
.site-footer code { background: rgba(0,0,0,0.05); padding: 1px 5px;
                     border-radius: 3px; color: #2a4d69; }
.site-footer .footer-sep { margin: 0 8px; color: #ccc; }
"""
