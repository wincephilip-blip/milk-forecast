"""純時間序列預測 pipeline（不需跑場別 bottom-up）。

提供「快速預測」入口：
  - 只把 DHI 加總成時間序列
  - 跑所有可用 TS 模型
  - 加總到全國 / 各區域
  - 秒級完成
"""
import json
import logging
import time
import pandas as pd
import numpy as np
from datetime import datetime
from pathlib import Path

from .. import config, __version__
from ..data import load_combined
from .timeseries import (build_national_monthly_series, forecast_all,
                          ensemble_forecast)

log = logging.getLogger("milkfc.timeseries")


def build_regional_series(df_dhi, farm_to_county_map,
                            include_counties: bool = True,
                            min_farms_per_county: int = 5,
                            min_months: int = 36):
    """把 DHI 切成多層級時間序列：全國 / macro 4 區 / 主要縣市。

    Returns: dict
        '全國': Series
        '北','中','南','東': Series
        '彰化縣','雲林縣',... : Series（資料夠的縣市）
    """
    from ..data.national_stats import COUNTY_TO_MACRO

    df = df_dhi.copy()
    df["farm_id"] = df["farm_id"].astype(str)
    df["county"] = df["farm_id"].map(farm_to_county_map)
    df["macro"] = df["county"].map(COUNTY_TO_MACRO).fillna("其他")

    # 全國門檻較高（100 場/月、過濾早期不代表性紀錄）
    series_dict = {"全國": build_national_monthly_series(
        df, min_farms_per_month=100, min_records_per_month=500)}

    # Macro 區域用較低門檻（5 場/月）
    for macro in ["北","中","南","東"]:
        sub = df[df["macro"] == macro]
        if len(sub) > 100:
            s = build_national_monthly_series(
                sub, min_farms_per_month=5, min_records_per_month=30)
            if len(s) >= 24:
                series_dict[macro] = s

    # 縣市門檻最低（3 場/月）
    if include_counties:
        for county, sub in df[df["county"].notna()].groupby("county"):
            n_farms = sub["farm_id"].nunique()
            if n_farms < min_farms_per_county:
                continue
            s = build_national_monthly_series(
                sub, min_farms_per_month=3, min_records_per_month=20)
            if len(s) < min_months:
                continue
            series_dict[county] = s

    return series_dict


def _apply_calibration_static(series, ts_results, ensemble, sf_static):
    """用單一靜態 scale factor 校正（給區域用）。"""
    sf = float(sf_static)
    hist_cal = [{"yyyymm": idx, "value": float(v) * sf}
                 for idx, v in series.items()]

    models_cal = []
    for r in ts_results:
        if not r.get("success"):
            models_cal.append(r)
            continue
        new_fc = [{"yyyymm": pt["yyyymm"], "p50": pt["p50"]*sf,
                    "p10": pt["p10"]*sf, "p90": pt["p90"]*sf}
                   for pt in r["forecast"]]
        models_cal.append({**r, "forecast": new_fc})

    ens_cal = None
    if ensemble:
        new_fc = [{"yyyymm": pt["yyyymm"], "p50": pt["p50"]*sf,
                    "p10": pt["p10"]*sf, "p90": pt["p90"]*sf}
                   for pt in ensemble["forecast"]]
        ens_cal = {**ensemble, "forecast": new_fc}

    return {
        "series_history": hist_cal,
        "models": models_cal,
        "ensemble": ens_cal,
        "scale_factor": sf,
    }


def _apply_calibration(series, ts_results, ensemble, monthly_sf):
    """把預測結果乘上對應月份的 scale factor，產出全國尺度估計。"""
    def sf_for(ym):
        info = monthly_sf.get(ym, {})
        if isinstance(info, dict):
            return info.get("scale_factor", 2.23)
        return info or 2.23

    # 歷史序列校正
    hist_cal = [{"yyyymm": idx, "value": float(v) * sf_for(idx)}
                 for idx, v in series.items()]

    # 各模型預測校正
    models_cal = []
    for r in ts_results:
        if not r.get("success"):
            models_cal.append(r)
            continue
        new_fc = []
        for pt in r["forecast"]:
            sf = sf_for(pt["yyyymm"])
            new_fc.append({
                "yyyymm": pt["yyyymm"],
                "p50": pt["p50"] * sf,
                "p10": pt["p10"] * sf,
                "p90": pt["p90"] * sf,
            })
        models_cal.append({**r, "forecast": new_fc})

    ens_cal = None
    if ensemble:
        new_fc = []
        for pt in ensemble["forecast"]:
            sf = sf_for(pt["yyyymm"])
            new_fc.append({
                "yyyymm": pt["yyyymm"],
                "p50": pt["p50"] * sf,
                "p10": pt["p10"] * sf,
                "p90": pt["p90"] * sf,
            })
        ens_cal = {**ensemble, "forecast": new_fc}

    return {
        "series_history": hist_cal,
        "models": models_cal,
        "ensemble": ens_cal,
        "scale_factor_summary": {
            "national_avg": np.mean([sf_for(m) for m in
                                      [pt["yyyymm"] for pt in (ensemble or {}).get("forecast", [])]
                                      ]) if ensemble else None,
        },
    }


def run_timeseries_only(reference_date: str = None,
                         horizon_months: int = None,
                         include_regions: bool = True,
                         cache_path: Path = None,
                         include_calibration: bool = True,
                         with_neural: bool = False) -> dict:
    """純時間序列預測 pipeline。

    Args:
        reference_date: 強制基準日（YYYY-MM-DD），預設用最新
        horizon_months: 預測月數，預設 12
        include_regions: 是否同時跑各區域

    Returns:
        {snapshot_id, snapshot_dir, results}
    """
    t_start = time.time()
    snapshot_id = "ts_" + datetime.now().strftime("%Y%m%dT%H%M%S")
    horizon = horizon_months or config.HORIZON_MONTHS

    log.info(f"=== Time-series only pipeline {snapshot_id} ===")
    log.info(f"  horizon={horizon} months, include_regions={include_regions}")

    # 載入
    df = load_combined(cache_path or (config.SNAPSHOT_DIR / "_cache.pkl"))
    log.info(f"  Loaded {len(df):,} rows")

    # 截到 reference_date 之前的資料當訓練
    if reference_date:
        cutoff = pd.Timestamp(reference_date)
        df = df[df["sample_date"] <= cutoff]
        log.info(f"  Using data up to {cutoff.date()}")

    # 篩活躍場（最近 180 天有測乳）
    overall_max = df["sample_date"].max()
    active_cutoff = overall_max - pd.Timedelta(days=180)
    farm_latest = df.groupby("farm_id")["sample_date"].max()
    active = farm_latest[farm_latest >= active_cutoff].index.tolist()
    df = df[df["farm_id"].isin(active)]
    log.info(f"  Active farms: {len(active)}")

    # 縣市對應（給區域分組用）
    if include_regions:
        try:
            from ..calibration import map_farm_to_county
            farm_county = map_farm_to_county(df)
        except Exception:
            farm_county = {}
            include_regions = False
    else:
        farm_county = {}

    # 建立各區域時間序列
    if include_regions:
        series_dict = build_regional_series(df, farm_county)
    else:
        series_dict = {"全國": build_national_monthly_series(df)}

    log.info(f"  Built series for: {list(series_dict.keys())}")

    # 取得月度動態 scale factor（全國 + 區域）
    monthly_sf = {}
    region_static_sf = {}
    if include_calibration:
        try:
            from ..calibration import compute_monthly_scale_factors, compute_coverage_rates
            ref = pd.Timestamp(reference_date) if reference_date else df["sample_date"].max()
            future_months = [(ref + pd.DateOffset(months=h)).strftime("%Y-%m")
                             for h in range(1, horizon + 1)]
            past_months = [m.strftime("%Y-%m") for m in
                           pd.date_range(end=ref, periods=84, freq="MS")]
            all_months = past_months + future_months
            monthly_sf = compute_monthly_scale_factors(df, all_months)

            # 各區域靜態 scale factor（暫無區域月度動態）
            cov = compute_coverage_rates(df)
            region_static_sf = dict(cov.get("scale_factor_by_macro", {}))
            region_static_sf["全國"] = cov.get("scale_factor_national", 2.23)
            # 縣市 scale factor 也加進來
            region_static_sf.update(cov.get("scale_factor_by_county", {}))

            log.info(f"  Region scale factors: {len(region_static_sf)} regions")
        except Exception as e:
            log.warning(f"  Coverage calibration not available: {e}")

    # 對每條序列跑所有 TS 模型
    all_results = {}
    for region, series in series_dict.items():
        log.info(f"\n--- 區域: {region} ({len(series)} 個月) ---")
        # NeuralProphet 訓練慢、只在全國跑（避免區域訓練爆炸）
        use_neural_here = with_neural and (region == "全國")
        ts_results = forecast_all(series, horizon=horizon,
                                     with_neural=use_neural_here)
        ensemble = ensemble_forecast(ts_results)

        # log
        for r in ts_results:
            if r.get("success"):
                log.info(f"  {r['model']:<18} in-sample MAPE = {r.get('in_sample_mape',0):.1f}%")
            else:
                log.info(f"  {r['model']:<18} FAILED ({r.get('error','?')})")

        # 計算校正後序列（全國尺度估計）
        calibrated = None
        if include_calibration:
            if region == "全國" and monthly_sf:
                # 全國用月度動態 scale factor
                calibrated = _apply_calibration(series, ts_results, ensemble, monthly_sf)
            elif region in region_static_sf:
                # 區域用靜態 scale factor（暫無區域月度動態）
                sf_static = region_static_sf[region]
                calibrated = _apply_calibration_static(
                    series, ts_results, ensemble, sf_static)

        all_results[region] = {
            "series_history": [{"yyyymm": idx, "value": float(v)}
                                for idx, v in series.items()],
            "models": ts_results,
            "ensemble": ensemble,
            "calibrated": calibrated,  # 全國校正版（只在 region == "全國" 時有值）
        }

    # 存 snapshot
    config.SNAPSHOT_DIR.mkdir(exist_ok=True)
    snap_dir = config.SNAPSHOT_DIR / snapshot_id
    snap_dir.mkdir(exist_ok=True)

    manifest = {
        "snapshot_id": snapshot_id,
        "snapshot_type": "timeseries_only",
        "timestamp": datetime.now().isoformat(),
        "package_version": __version__,
        "config": {
            "reference_date": reference_date or str(overall_max.date()),
            "horizon_months": horizon,
            "regions": list(series_dict.keys()),
            "n_active_farms": len(active),
        },
        "elapsed_seconds": time.time() - t_start,
    }

    with open(snap_dir / "manifest.json", "w") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False, default=str)
    with open(snap_dir / "ts_results.json", "w") as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False, default=str)

    log.info(f"\nSnapshot: {snap_dir}")
    log.info(f"Total elapsed: {time.time()-t_start:.0f}s")

    return {
        "snapshot_id": snapshot_id,
        "snapshot_dir": str(snap_dir),
        "results": all_results,
        "manifest": manifest,
    }
