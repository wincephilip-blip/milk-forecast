"""主 pipeline：從原始 DHI 跑到預測快照與儀表板."""
import pandas as pd
import numpy as np
import json
import hashlib
import logging
import time
import pickle
from datetime import datetime
from pathlib import Path

from . import config, __version__, __model_version__
from .data import load_combined, validate_dhi
from .models import train_models
from .forecast import forecast_with_bootstrap
from .diagnostics import detect_anomalies, run_backtest, actual_monthly_milk

log = logging.getLogger("milkfc.pipeline")

def _data_hash(df: pd.DataFrame) -> str:
    """計算資料 hash（用於版本管理）。"""
    cols = ["farm_id","cow_id","sample_date","milk_kg"]
    s = pd.util.hash_pandas_object(df[cols], index=False).values.tobytes()
    return hashlib.sha256(s).hexdigest()[:16]

def _serialize_models(models: dict) -> dict:
    """把 models dict 轉 JSON-safe（去除 dataframe）"""
    safe = {}
    for k, v in models.items():
        if k.startswith("_"):
            continue
        if isinstance(v, dict):
            safe[k] = {str(kk): (list(vv) if isinstance(vv, tuple) else vv)
                       for kk, vv in v.items()}
        else:
            safe[k] = v
    return safe

def select_best_window(df_farm, val_year, candidates=None, n_sim_quick=10):
    """對單一場做「內部回測」找最佳訓練視窗。

    用 (val_year - 1) 當作小型測試年：訓練資料截止 (val_year - 2)-12-31，
    預測 (val_year - 1) 並對照實際，看哪個視窗 MAPE 最低。

    Returns: (best_window_months, all_results dict)
    """
    candidates = candidates or config.AUTO_WINDOW_CANDIDATES
    inner_train_end = pd.Timestamp(f"{val_year-2}-12-31")
    d_inner = df_farm[df_farm["sample_date"] <= inner_train_end]
    if len(d_inner) < 200:
        return (config.TRAIN_WINDOW_MONTHS, {})

    actual = actual_monthly_milk(df_farm,
        f"{val_year-1}-01-01", f"{val_year-1}-12-31")
    if not actual:
        return (config.TRAIN_WINDOW_MONTHS, {})

    results = {}
    for w in candidates:
        cutoff = inner_train_end - pd.DateOffset(months=w)
        d_w = d_inner[d_inner["sample_date"] >= cutoff]
        if len(d_w) < 100:
            continue
        try:
            m = train_models(d_w)
            fc, _ = forecast_with_bootstrap(d_w, inner_train_end, 12, m,
                                              n_sim=n_sim_quick)
            bt = run_backtest(fc, actual, val_year - 1)
            results[w] = bt["mape"] if bt["mape"] is not None else 999
        except Exception:
            continue

    if not results:
        return (config.TRAIN_WINDOW_MONTHS, {})
    best = min(results, key=results.get)
    return (best, results)


def run_pipeline(
    farm_ids: list = None,
    train_end: str = None,
    target_year: int = None,
    backtest: bool = True,
    n_sim: int = None,
    cache_path: Path = None,
    mode: str = "backtest",
    train_window_months: int = None,
    auto_window: bool = None,
    horizon_months: int = None,    # 預測時程，預設 12
    reference_date: str = None,    # 強制以某日為「現在」基準
) -> dict:
    """
    完整跑一次：載入 → 驗證 → 對每場訓練+預測+異常偵測 → 存快照
    Returns: {snapshot_id, results, validation, ...}
    """
    t_start = time.time()
    snapshot_id = datetime.now().strftime("%Y%m%dT%H%M%S")
    log.info(f"=== Pipeline run {snapshot_id} ===")

    # 1. Load
    df = load_combined(cache_path or (config.SNAPSHOT_DIR / "_cache.pkl"))
    log.info(f"Loaded {len(df):,} rows, {df['farm_id'].nunique()} farms")

    # 2. Validate
    val = validate_dhi(df)
    log.info(f"Validation: {len(val['warnings'])} warnings")
    for w in val["warnings"]:
        log.warning(f"  [VALIDATION] {w}")

    # 3. 決定處理場：只挑「目前還在運作的場」
    # 篩選邏輯：該場最近一筆測乳在「整體資料最新日 - 6 個月」內
    # 已退場的場（沒有近期測乳）會自動排除，避免污染預測樣本
    if farm_ids is None:
        ref = (pd.Timestamp(reference_date) if reference_date
               else df["sample_date"].max())
        active_cutoff = ref - pd.Timedelta(days=180)
        farm_latest = df.groupby("farm_id")["sample_date"].max()
        active_farms = farm_latest[farm_latest >= active_cutoff].index.tolist()
        farm_ids = sorted(active_farms)
        n_total = df["farm_id"].nunique()
        n_inactive = n_total - len(farm_ids)
        log.info(f"Processing {len(farm_ids)} 目前運作中的場 "
                 f"(排除 {n_inactive} 場：最近 180 天無測乳)")
    else:
        log.info(f"Processing {len(farm_ids)} farms (使用者指定)")

    # 3.5 場別分類 + 計算 segment priors（用於小場 shrinkage）
    from .segmentation import classify_farms, compute_segment_priors, apply_segment_prior
    log.info("Classifying farms into segments...")
    classification = classify_farms(df)
    seg_by_farm = dict(zip(classification["farm_id"], classification["segment"]))
    seg_priors = compute_segment_priors(df, classification)
    log.info(f"  {len(seg_priors)} segments with priors")

    # 3.6 場別 → 縣市 對應（用於 THI 氣象修正）
    try:
        from .calibration import map_farm_to_county
        farm_to_county = map_farm_to_county(df)
        log.info(f"  {len(farm_to_county)} farms mapped to counties (for THI)")
    except Exception:
        farm_to_county = {}

    # 4. 對每場跑
    if mode == "production":
        backtest = False
        log.info("Mode: production (predict 12 months from each farm's latest data)")
    elif mode == "combined":
        log.info("Mode: combined (歷史驗證 + 未來預測，每場跑兩 pass)")
    else:
        train_end = train_end or "2023-12-31"
        target_year = target_year or 2024
        log.info(f"Mode: backtest (train_end={train_end}, target={target_year})")

    results = []
    for fid in farm_ids:
        df_farm = df[df["farm_id"] == fid]
        if len(df_farm) < 200:
            log.warning(f"  {fid}: 跳過（資料不足 {len(df_farm)} 筆）")
            continue
        try:
            t0 = time.time()

            if mode == "combined":
                # ========== 第一 pass: 歷史驗證 ==========
                # 允許以 reference_date 強制指定「現在」基準（例如：以 2025-12-31 為基準）
                if reference_date:
                    latest_data = pd.Timestamp(reference_date)
                else:
                    latest_data = df_farm["sample_date"].max()
                # 自動決定驗證年份：取最近完整年（latest 是該年 12 月才完整）
                if latest_data.month == 12:
                    val_year = latest_data.year
                else:
                    val_year = latest_data.year - 1

                # 自動選最佳訓練視窗（用 val_year - 1 做 inner backtest）
                effective_window = train_window_months or config.TRAIN_WINDOW_MONTHS
                use_auto = (auto_window if auto_window is not None
                            else config.AUTO_WINDOW)
                if use_auto and train_window_months is None:
                    best_w, win_results = select_best_window(df_farm, val_year)
                    if win_results:
                        effective_window = best_w
                        log.info(f"  {fid}: auto-window={best_w}m "
                                 f"(候選 MAPE: {[(w, f'{m:.1f}%') for w,m in win_results.items()]})")
                bt_train_end = pd.Timestamp(f"{val_year-1}-12-31")
                d_train_bt = df_farm[df_farm["sample_date"] <= bt_train_end]
                # 用 effective_window 限制訓練資料（curves 部分）
                bt_window_start = bt_train_end - pd.DateOffset(months=effective_window)
                d_train_bt_curves = d_train_bt[d_train_bt["sample_date"] >= bt_window_start]
                if len(d_train_bt_curves) < 100:
                    raise ValueError(f"驗證訓練資料不足: {len(d_train_bt_curves)}")
                farm_county = farm_to_county.get(str(fid))
                models_bt = train_models(d_train_bt_curves,
                                          farm_county=farm_county)
                # Shrinkage：小場用 segment prior 平滑
                seg_label = seg_by_farm.get(fid)
                # 保守 shrinkage：只對極小資料場（< 300 筆）做輕量平滑
                # 經驗顯示重 shrinkage 反而傷害管理特殊的場
                if seg_label and seg_label in seg_priors:
                    n_records = len(d_train_bt)
                    if n_records < 300:
                        shrink = 0.15  # 一律輕量
                        models_bt = apply_segment_prior(models_bt, seg_label, seg_priors, shrink)
                fc_bt, meta_bt = forecast_with_bootstrap(
                    d_train_bt, bt_train_end, horizon_months or config.HORIZON_MONTHS, models_bt,
                    n_sim=n_sim or config.N_SIMULATIONS)

                # Edge case: 預測為空時跳過
                if len(fc_bt) == 0 or "yyyymm" not in fc_bt.columns:
                    raise ValueError(f"驗證 pass 預測為空（無活躍牛或資料異常）")

                actual = actual_monthly_milk(df_farm,
                    f"{val_year}-01-01", f"{val_year}-12-31")
                bt = run_backtest(fc_bt, actual, val_year)
                fc_bt = bt["forecast"]
                # 只保留驗證年份月份
                fc_bt = fc_bt[fc_bt["yyyymm"].str.startswith(str(val_year))].copy()
                fc_bt["phase"] = "backtest"
                anom = detect_anomalies(fc_bt)

                # ========== 第二 pass: 未來預測 ==========
                prod_train_end = pd.Timestamp(f"{val_year}-12-31")
                if prod_train_end > latest_data:
                    prod_train_end = latest_data
                d_train_prod = df_farm[df_farm["sample_date"] <= prod_train_end]
                prod_window_start = prod_train_end - pd.DateOffset(months=effective_window)
                d_train_prod_curves = d_train_prod[d_train_prod["sample_date"] >= prod_window_start]
                models_prod = train_models(d_train_prod_curves,
                                            farm_county=farm_county)
                if seg_label and seg_label in seg_priors:
                    n_records = len(d_train_prod_curves)
                    if n_records < 300:
                        models_prod = apply_segment_prior(models_prod, seg_label, seg_priors, 0.15)
                fc_prod, meta_prod = forecast_with_bootstrap(
                    d_train_prod, prod_train_end, horizon_months or config.HORIZON_MONTHS, models_prod,
                    n_sim=n_sim or config.N_SIMULATIONS)

                # Edge case
                if len(fc_prod) == 0 or "yyyymm" not in fc_prod.columns:
                    raise ValueError(f"預測 pass 預測為空")

                # 只保留未來年份（驗證年份之後）
                fc_prod = fc_prod[fc_prod["yyyymm"] > f"{val_year}-12"].copy().head(12)
                fc_prod["actual"] = None
                fc_prod["err_pct"] = None
                fc_prod["phase"] = "production"

                # 合併
                fc_combined = pd.concat([fc_bt, fc_prod], ignore_index=True)

                res = {"farm_id": fid,
                       "models": _serialize_models(models_prod),  # 用最新訓練的
                       "forecast": fc_combined,
                       "meta": meta_prod,
                       "mode": "combined",
                       "data_latest": str(latest_data.date()),
                       "validation_year": val_year,
                       "validation_train_end": str(bt_train_end.date()),
                       "production_train_end": str(prod_train_end.date()),
                       "backtest": {k: v for k, v in bt.items() if k != "forecast"},
                       "anomaly": anom,
                       "n_records": int(len(df_farm)),
                       "segment": seg_label,
                       "train_window_months": int(effective_window)}

            else:
                # 原本的 backtest / production 路徑
                if mode == "production":
                    farm_train_end = df_farm["sample_date"].max()
                else:
                    farm_train_end = pd.Timestamp(train_end)
                train_end_ts = farm_train_end

                d_train = df_farm[df_farm["sample_date"] <= train_end_ts]
                models = train_models(d_train)
                fc, meta = forecast_with_bootstrap(
                    d_train, train_end_ts, horizon_months or config.HORIZON_MONTHS, models,
                    n_sim=n_sim or config.N_SIMULATIONS)

                res = {"farm_id": fid,
                       "models": _serialize_models(models),
                       "forecast": fc, "meta": meta,
                       "mode": mode,
                       "data_latest": str(df_farm["sample_date"].max().date()),
                       "train_end": str(train_end_ts.date()),
                       "n_records": int(len(df_farm))}

                if backtest:
                    actual = actual_monthly_milk(df_farm,
                        f"{target_year}-01-01", f"{target_year}-12-31")
                    bt = run_backtest(fc, actual, target_year)
                    res["backtest"] = {k: v for k, v in bt.items() if k != "forecast"}
                    fc_w_actual = bt["forecast"]
                    anom = detect_anomalies(fc_w_actual)
                    res["anomaly"] = anom
                    res["forecast"] = fc_w_actual

            elapsed = time.time() - t0
            log.info(
                f"  {fid}: MAPE={res.get('backtest',{}).get('mape',0):.1f}% "
                f"bias={res.get('backtest',{}).get('bias',0):+.1f}% "
                f"cov={res.get('backtest',{}).get('coverage',0)*100:.0f}% "
                f"anom={res.get('anomaly',{}).get('severity','-')} ({elapsed:.0f}s)")
            results.append(res)
        except Exception as e:
            log.error(f"  {fid}: 失敗 - {e}")
            results.append({"farm_id": fid, "error": str(e)})

    # 4.5 跑 Top-Down 時間序列模型（互相驗證）
    log.info("Running Top-Down time series models...")
    try:
        from .forecast import (build_national_monthly_series, forecast_all,
                                ensemble_forecast)
        nat_series = build_national_monthly_series(df)
        ts_results = forecast_all(nat_series,
                                    horizon=horizon_months or config.HORIZON_MONTHS)
        ensemble = ensemble_forecast(ts_results)
        log.info(f"  Top-Down models: {[r['model'] for r in ts_results if r.get('success')]}")
        for r in ts_results:
            if r.get("success") and r.get("in_sample_mape"):
                log.info(f"    {r['model']}: in-sample MAPE={r['in_sample_mape']:.1f}%")
    except Exception as e:
        log.warning(f"Top-Down models failed: {e}")
        ts_results = []
        ensemble = None

    # 5. 產生快照
    snapshot = {
        "snapshot_id": snapshot_id,
        "timestamp": datetime.now().isoformat(),
        "package_version": __version__,
        "model_version": __model_version__,
        "data_hash": _data_hash(df),
        "validation": val,
        "config": {
            "mode": mode,
            "train_end": train_end if mode == "backtest" else "(per-farm latest)",
            "target_year": target_year if mode == "backtest" else None,
            "horizon_months": config.HORIZON_MONTHS,
            "n_simulations": n_sim or config.N_SIMULATIONS,
            "n_bootstrap": config.N_BOOTSTRAP,
            "anomaly_threshold_pct": config.ANOMALY_BIAS_THRESHOLD,
        },
        "n_farms_processed": len([r for r in results if "error" not in r]),
        "n_farms_failed": len([r for r in results if "error" in r]),
        "elapsed_seconds": time.time() - t_start,
    }

    # 6. 儲存
    config.SNAPSHOT_DIR.mkdir(exist_ok=True)
    snap_dir = config.SNAPSHOT_DIR / snapshot_id
    snap_dir.mkdir(exist_ok=True)

    with open(snap_dir / "manifest.json", "w") as f:
        json.dump(snapshot, f, indent=2, ensure_ascii=False, default=str)

    # 預測結果用 pickle 存（含 dataframe）
    with open(snap_dir / "results.pkl", "wb") as f:
        pickle.dump(results, f)

    # Top-Down 時序預測結果存 JSON
    if ts_results or ensemble:
        with open(snap_dir / "topdown_forecast.json", "w") as f:
            json.dump({"models": ts_results, "ensemble": ensemble}, f,
                       indent=2, ensure_ascii=False, default=str)

    # 簡化版預測 csv（給 audit）
    fcs = []
    for r in results:
        if "forecast" in r and isinstance(r["forecast"], pd.DataFrame):
            fc = r["forecast"].copy()
            fc["farm_id"] = r["farm_id"]
            fcs.append(fc)
    if fcs:
        all_fc = pd.concat(fcs, ignore_index=True)
        all_fc.to_csv(snap_dir / "forecasts.csv", index=False)

    log.info(f"Snapshot saved to {snap_dir}")
    log.info(f"Total elapsed: {time.time()-t_start:.0f}s")

    return {"snapshot_id": snapshot_id, "snapshot": snapshot,
            "results": results, "snapshot_dir": str(snap_dir)}
