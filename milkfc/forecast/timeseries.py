"""Top-Down 時間序列模型集合。

把全國/區域月乳量當一條時間序列、用經典時間序列方法預測。
與 bottom-up（場別累加）互相驗證。

可用模型（自動偵測哪些套件可用）：
  - naive_seasonal    : 最簡單，去年同月（基準線）
  - stl_linear        : 純 numpy 的 STL 分解 + 線性外推
  - holt_winters      : Holt-Winters 三重指數平滑（需 statsmodels）
  - sarima            : SARIMA(1,1,1)(1,1,1,12)（需 statsmodels）
  - prophet           : Facebook Prophet（需 prophet）

每個模型輸入相同：pandas Series indexed by 'yyyymm' (str)
輸出: dict {forecast: [(yyyymm, p50, p10, p90), ...], in_sample_mape, model_name}
"""
import pandas as pd
import numpy as np
import warnings


def build_national_monthly_series(df_dhi, calibrated=False, scale_factors=None,
                                     min_farms_per_month: int = 30,
                                     min_records_per_month: int = 100):
    """從 DHI 加總成全國月乳量時間序列。

    Args:
        df_dhi: DHI 原始資料
        calibrated: True → 乘以 scale factor（外推到真實全國）
        scale_factors: {yyyymm: factor} 月度動態係數
        min_farms_per_month: 該月至少 N 場才納入（過濾資料污染期）
        min_records_per_month: 該月至少 N 筆紀錄才納入
    Returns:
        pd.Series indexed by yyyymm string, value=月乳量 kg
    """
    df = df_dhi.copy()
    df = df[df["sample_date"].notna() & df["milk_kg"].notna()]
    df["yyyymm"] = df["sample_date"].dt.to_period("M").astype(str)

    # 過濾「樣本量不足」的月份（避免 DHI 早期 / 異常月份污染）
    farm_count = df.groupby("yyyymm")["farm_id"].nunique()
    record_count = df.groupby("yyyymm").size()
    valid_months = farm_count[
        (farm_count >= min_farms_per_month) &
        (record_count >= min_records_per_month)
    ].index
    df = df[df["yyyymm"].isin(valid_months)]

    if df.empty:
        return pd.Series(dtype=float)

    # 月乳量 = 每頭該月日均 × 30
    monthly_per_cow = df.groupby(["yyyymm","cow_id"])["milk_kg"].mean()
    monthly = (monthly_per_cow.groupby(level=0).sum() * 30)

    if calibrated and scale_factors:
        monthly = monthly * monthly.index.map(
            lambda m: scale_factors.get(m, {}).get("scale_factor", 1.0)
            if isinstance(scale_factors.get(m), dict)
            else scale_factors.get(m, 1.0))

    return monthly.sort_index()


def forecast_naive_seasonal(series: pd.Series, horizon: int = 12) -> dict:
    """最簡單的基準線：直接用「去年同月」當預測。"""
    if len(series) < 12:
        return None

    # 預測點 = 去年同月 × (近 1 年增長率)
    last_12 = series.iloc[-12:]
    prev_12 = series.iloc[-24:-12] if len(series) >= 24 else last_12
    growth = (last_12.mean() / prev_12.mean()) if prev_12.mean() > 0 else 1.0

    last_idx = pd.Period(series.index[-1])
    forecast = []
    for h in range(1, horizon + 1):
        future_idx = (last_idx + h).strftime("%Y-%m")
        # 找對應的「去年同月」
        future_month = (last_idx + h).month
        # 去年同月（在 series 中）
        past_idx_str = (last_idx + h - 12).strftime("%Y-%m")
        if past_idx_str in series.index:
            past_value = series.loc[past_idx_str]
        else:
            past_value = series.iloc[-12 + (h - 1) % 12]
        p50 = past_value * growth
        forecast.append({
            "yyyymm": future_idx, "p50": p50,
            "p10": p50 * 0.9, "p90": p50 * 1.1,
        })

    # 殘差驗證
    in_sample = _in_sample_mape_naive(series, lookback=12)
    return {
        "model": "naive_seasonal",
        "forecast": forecast,
        "in_sample_mape": in_sample,
        "success": True,
    }


def _in_sample_mape_naive(series, lookback=12):
    """In-sample MAPE: 用前面資料回測最後 12 個月。"""
    if len(series) < lookback * 2:
        return None
    train = series.iloc[:-lookback]
    test = series.iloc[-lookback:]
    pred = []
    for i, idx in enumerate(test.index):
        # 預測 = 去年同月
        prev_year = (pd.Period(idx) - 12).strftime("%Y-%m")
        pred.append(series.loc[prev_year] if prev_year in series.index else test.mean())
    pred = pd.Series(pred, index=test.index)
    err = ((pred - test) / test).abs() * 100
    return float(err.mean())


def forecast_stl_linear(series: pd.Series, horizon: int = 12,
                          season_period: int = 12) -> dict:
    """STL 分解（statsmodels.tsa.seasonal.STL）+ 線性趨勢外推。

    使用 statsmodels 之 STL 做 LOESS-based 分解，
    然後對 trend 線性外推、season 取最後一個 cycle 重複、residual 給不確定性。
    """
    try:
        from statsmodels.tsa.seasonal import STL
    except ImportError:
        return {"model": "stl_linear", "success": False,
                "error": "statsmodels not installed"}

    if len(series) < season_period * 2:
        return None

    y = series.values.astype(float)
    n = len(y)

    try:
        stl_result = STL(y, period=season_period, robust=True).fit()
        trend = stl_result.trend
        seasonal = stl_result.seasonal
        residual = stl_result.resid

        x = np.arange(n)
        coef = np.polyfit(x, trend, 1)
        sigma = float(np.std(residual))

        last_idx = pd.Period(series.index[-1])
        forecast = []
        for h in range(1, horizon + 1):
            future_pd = last_idx + h
            future_x = n + h - 1
            trend_h = coef[0] * future_x + coef[1]
            # 季節項取最後一個完整 cycle 的對應位置
            season_idx = (n - season_period) + ((h - 1) % season_period)
            season_h = float(seasonal[season_idx]) if 0 <= season_idx < n else 0.0
            p50 = float(trend_h + season_h)
            forecast.append({
                "yyyymm": future_pd.strftime("%Y-%m"),
                "p50": p50,
                "p10": p50 - 1.28 * sigma,
                "p90": p50 + 1.28 * sigma,
            })

        fitted = trend + seasonal
        err = ((fitted - y) / y).clip(-1, 1)
        in_sample = float(np.abs(err).mean()) * 100

        return {
            "model": "stl_linear",
            "forecast": forecast,
            "in_sample_mape": in_sample,
            "trend_slope_per_month": float(coef[0]),
            "trend_per_year_pct": float(coef[0] * 12 / np.mean(y) * 100),
            "success": True,
        }
    except Exception as e:
        return {"model": "stl_linear", "success": False, "error": str(e)}


def forecast_holt_winters(series: pd.Series, horizon: int = 12) -> dict:
    """Holt-Winters 三重指數平滑（季節 multiplicative）。"""
    try:
        from statsmodels.tsa.holtwinters import ExponentialSmoothing
    except ImportError:
        return {"model": "holt_winters", "success": False,
                "error": "statsmodels not installed"}

    if len(series) < 24:
        return None

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            model = ExponentialSmoothing(
                series.values, seasonal_periods=12,
                trend="add", seasonal="mul", initialization_method="estimated")
            fit = model.fit()
            fc = fit.forecast(horizon)

        # 用 in-sample residual 估不確定性
        resid = fit.fittedvalues - series.values
        sigma = np.std(resid)

        last_idx = pd.Period(series.index[-1])
        forecast = []
        for h, p50 in enumerate(fc, 1):
            forecast.append({
                "yyyymm": (last_idx + h).strftime("%Y-%m"),
                "p50": float(p50),
                "p10": float(p50 - 1.28 * sigma),
                "p90": float(p50 + 1.28 * sigma),
            })

        in_sample = float(np.abs(resid / series.values).mean() * 100)
        return {
            "model": "holt_winters",
            "forecast": forecast,
            "in_sample_mape": in_sample,
            "success": True,
        }
    except Exception as e:
        return {"model": "holt_winters", "success": False, "error": str(e)}


def forecast_sarima(series: pd.Series, horizon: int = 12) -> dict:
    """SARIMA 模型（pmdarima.auto_arima 自動選階）。

    用 pmdarima.auto_arima 對 (p, d, q)(P, D, Q)_12 做網格搜尋：
      - 以 KPSS 檢定決定差分階數 d、OCSB 決定季節差分 D
      - 以 AIC 選最佳 (p, q, P, Q)
      - 搜尋範圍：p ≤ 3, q ≤ 3, P ≤ 2, Q ≤ 2
    """
    try:
        import pmdarima as pm
    except ImportError:
        return {"model": "sarima", "success": False,
                "error": "pmdarima not installed (pip install pmdarima)"}

    if len(series) < 24:
        return None

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            arima = pm.auto_arima(
                series.values,
                start_p=0, max_p=3,
                start_q=0, max_q=3,
                start_P=0, max_P=2,
                start_Q=0, max_Q=2,
                d=None, D=None,           # 由 KPSS 與 OCSB 自動決定
                m=12,                      # 月度季節
                seasonal=True,
                test="kpss", seasonal_test="ocsb",
                information_criterion="aic",
                stepwise=True,
                suppress_warnings=True,
                error_action="ignore",
                maxiter=200,
            )
            mean, ci = arima.predict(n_periods=horizon, return_conf_int=True, alpha=0.20)

        last_idx = pd.Period(series.index[-1])
        forecast = []
        for h in range(horizon):
            forecast.append({
                "yyyymm": (last_idx + h + 1).strftime("%Y-%m"),
                "p50": float(mean[h]),
                "p10": float(ci[h][0]),
                "p90": float(ci[h][1]),
            })

        in_sample_pred = arima.predict_in_sample()
        err = (in_sample_pred - series.values) / series.values
        in_sample = float(np.abs(err[~np.isnan(err)]).mean() * 100)

        order = arima.order
        seasonal_order = arima.seasonal_order

        return {
            "model": "sarima",
            "forecast": forecast,
            "in_sample_mape": in_sample,
            "aic": float(arima.aic()),
            "selected_order": list(order),
            "selected_seasonal_order": list(seasonal_order),
            "success": True,
        }
    except Exception as e:
        return {"model": "sarima", "success": False, "error": str(e)}


def forecast_sarima_thi(series: pd.Series, horizon: int = 12) -> dict:
    """SARIMAX with THI 氣溫濕度指數作為外生變數。"""
    try:
        from statsmodels.tsa.statespace.sarimax import SARIMAX
        from ..data.weather import get_national_thi_monthly
    except ImportError as e:
        return {"model": "sarima_thi", "success": False,
                "error": f"missing dep: {e}"}

    if len(series) < 24:
        return None

    try:
        # 為訓練期建 THI 序列
        train_months = [str(idx) for idx in series.index]
        thi_train = get_national_thi_monthly(train_months).values

        # 預測期 THI
        last_idx = pd.Period(series.index[-1])
        fc_months = [(last_idx + h + 1).strftime("%Y-%m") for h in range(horizon)]
        thi_fc = get_national_thi_monthly(fc_months).values

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            model = SARIMAX(series.values,
                            exog=thi_train.reshape(-1, 1),
                            order=(1, 1, 1),
                            seasonal_order=(1, 1, 1, 12),
                            enforce_stationarity=False,
                            enforce_invertibility=False)
            fit = model.fit(disp=False, maxiter=200)
            fc_obj = fit.get_forecast(horizon, exog=thi_fc.reshape(-1, 1))
            mean = fc_obj.predicted_mean
            ci = fc_obj.conf_int(alpha=0.20)

        forecast = []
        for h in range(horizon):
            forecast.append({
                "yyyymm": fc_months[h],
                "p50": float(mean[h]),
                "p10": float(ci[h][0]),
                "p90": float(ci[h][1]),
            })

        fitted = fit.fittedvalues
        err = (fitted - series.values) / series.values
        in_sample = float(np.abs(err[~np.isnan(err)]).mean() * 100)

        return {
            "model": "sarima_thi",
            "forecast": forecast,
            "in_sample_mape": in_sample,
            "aic": float(fit.aic),
            "success": True,
        }
    except Exception as e:
        return {"model": "sarima_thi", "success": False, "error": str(e)}


def forecast_prophet_thi(series: pd.Series, horizon: int = 12) -> dict:
    """Prophet with THI as additional regressor."""
    try:
        from prophet import Prophet
        from ..data.weather import get_national_thi_monthly
    except ImportError as e:
        return {"model": "prophet_thi", "success": False,
                "error": f"missing dep: {e}"}

    if len(series) < 24:
        return None

    try:
        train_months = [str(idx) for idx in series.index]
        thi_train = get_national_thi_monthly(train_months).values

        df = pd.DataFrame({
            "ds": [pd.Timestamp(str(idx)) for idx in series.index],
            "y": series.values,
            "thi": thi_train,
        })

        m = Prophet(yearly_seasonality=True, weekly_seasonality=False,
                     daily_seasonality=False, interval_width=0.80)
        m.add_regressor("thi")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            m.fit(df)

        last_idx = pd.Period(series.index[-1])
        fc_months = [(last_idx + h + 1).strftime("%Y-%m") for h in range(horizon)]
        thi_fc = get_national_thi_monthly(fc_months).values

        future = pd.DataFrame({
            "ds": [pd.Timestamp(ym + "-15") for ym in fc_months],
            "thi": thi_fc,
        })
        fc = m.predict(future)

        forecast = []
        for h in range(horizon):
            forecast.append({
                "yyyymm": fc_months[h],
                "p50": float(fc.iloc[h]["yhat"]),
                "p10": float(fc.iloc[h]["yhat_lower"]),
                "p90": float(fc.iloc[h]["yhat_upper"]),
            })

        # In-sample MAPE
        in_sample = float(np.abs(
            (m.predict(df)["yhat"].values - series.values) / series.values
        ).mean() * 100)

        return {
            "model": "prophet_thi",
            "forecast": forecast,
            "in_sample_mape": in_sample,
            "success": True,
        }
    except Exception as e:
        return {"model": "prophet_thi", "success": False, "error": str(e)}


def forecast_prophet(series: pd.Series, horizon: int = 12) -> dict:
    """Facebook Prophet."""
    try:
        from prophet import Prophet
    except ImportError:
        return {"model": "prophet", "success": False,
                "error": "prophet not installed"}

    if len(series) < 24:
        return None

    try:
        df = pd.DataFrame({
            "ds": pd.to_datetime(series.index + "-15"),  # 月中
            "y": series.values,
        })
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            m = Prophet(yearly_seasonality=True, weekly_seasonality=False,
                       daily_seasonality=False, interval_width=0.80)
            m.fit(df)
            future = m.make_future_dataframe(periods=horizon, freq="MS")
            future["ds"] = future["ds"] + pd.Timedelta(days=14)
            fc = m.predict(future)

        # 取最後 horizon 列
        fc_future = fc.tail(horizon)
        last_idx = pd.Period(series.index[-1])
        forecast = []
        for h, (_, row) in enumerate(fc_future.iterrows(), 1):
            forecast.append({
                "yyyymm": (last_idx + h).strftime("%Y-%m"),
                "p50": float(row["yhat"]),
                "p10": float(row["yhat_lower"]),
                "p90": float(row["yhat_upper"]),
            })

        # In-sample
        fitted = fc.head(len(series))
        err = (fitted["yhat"].values - series.values) / series.values
        in_sample = float(np.abs(err).mean() * 100)

        return {
            "model": "prophet",
            "forecast": forecast,
            "in_sample_mape": in_sample,
            "success": True,
        }
    except Exception as e:
        return {"model": "prophet", "success": False, "error": str(e)}


def forecast_neural_prophet(series: pd.Series, horizon: int = 12) -> dict:
    """NeuralProphet：Prophet 加上 PyTorch AR-Net 神經網路。

    需要：pip install neuralprophet
    特性：相比 Prophet 多了 AR-Net（學最近 N 期的 autoregressive 訊號）。
    風險：過擬合（NN 參數多）；訓練不穩定（種子敏感）。
    """
    try:
        from neuralprophet import NeuralProphet
        import logging
        # NeuralProphet 內部 log 太吵、降到 ERROR
        for log_name in ['neuralprophet', 'NP', 'nprophet']:
            logging.getLogger(log_name).setLevel(logging.ERROR)
    except ImportError:
        return {"model": "neural_prophet", "success": False,
                "error": "neural_prophet 環境未設定"}

    if len(series) < 36:
        return None

    try:
        df = pd.DataFrame({
            "ds": [pd.Timestamp(str(idx)) for idx in series.index],
            "y": series.values,
        })

        m = NeuralProphet(
            yearly_seasonality=True,
            weekly_seasonality=False,
            daily_seasonality=False,
            n_lags=12,             # AR 用過去 12 個月
            n_forecasts=horizon,
            quantiles=[0.10, 0.90],  # 給 P10 / P90
            epochs=50,
            learning_rate=0.001,
        )

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            m.set_plotting_backend("plotly-static")
            metrics = m.fit(df, freq="MS", progress=None)

            future = m.make_future_dataframe(df, periods=horizon,
                                                n_historic_predictions=False)
            fc = m.predict(future)

        # 先算 in-sample MAPE 給 fallback 用
        try:
            in_sample = float(metrics["MAE_val"].iloc[-1] / series.mean() * 100) \
                if "MAE_val" in metrics.columns else \
                float(metrics["MAE"].iloc[-1] / series.mean() * 100)
        except Exception:
            in_sample = 5.0

        # NP 的多步預測在 fc 中以 yhat1/yhat2/.../yhatN 多欄位呈現
        # 嘗試多種 quantile 欄位命名（NP 版本差異）
        def _find_q_col(h, q):
            """嘗試多種 NP quantile 欄位名"""
            candidates = [
                f"yhat{h} {q*100:.1f}%",   # 'yhat1 10.0%'
                f"yhat{h} {q*100:.0f}%",   # 'yhat1 10%'
                f"yhat{h} q{q:.2f}",       # 'yhat1 q0.10'
                f"yhat{h}_q{int(q*100)}",  # 'yhat1_q10'
            ]
            for c in candidates:
                if c in fc.columns:
                    return c
            return None

        forecast = []
        last_idx = pd.Period(series.index[-1])
        # NP quantile 失敗時的 fallback 比例：用 in-sample MAPE
        np_sigma_pct = max(in_sample / 100.0, 0.02)  # 至少 ±2%
        for h in range(1, horizon + 1):
            ym = (last_idx + h).strftime("%Y-%m")
            col_p50 = f"yhat{h}"
            col_p10 = _find_q_col(h, 0.10)
            col_p90 = _find_q_col(h, 0.90)
            row = fc.iloc[-(horizon - h + 1)] if (horizon - h + 1) <= len(fc) else fc.iloc[-1]
            try:
                p50 = float(row[col_p50])
            except (KeyError, ValueError):
                p50 = float(row.get("yhat", row.get("yhat1", 0)))

            # P10 / P90：先嘗試 NP 輸出、否則用 fallback
            try:
                p10 = float(row[col_p10]) if col_p10 else None
                p90 = float(row[col_p90]) if col_p90 else None
                if p10 is None or p90 is None or pd.isna(p10) or pd.isna(p90):
                    raise ValueError("quantile NaN")
                if abs(p10 - p50) < p50 * 0.001:  # 差異太小、視為無效
                    raise ValueError("quantile collapsed")
            except (KeyError, ValueError, TypeError):
                # NP quantile 失敗、用簡單 fallback：±1.28 × in-sample 殘差比例
                p10 = p50 * (1 - 1.28 * np_sigma_pct)
                p90 = p50 * (1 + 1.28 * np_sigma_pct)
            forecast.append({"yyyymm": ym, "p50": p50, "p10": p10, "p90": p90})

        return {
            "model": "neural_prophet",
            "forecast": forecast,
            "in_sample_mape": in_sample,
            "success": True,
        }
    except Exception as e:
        return {"model": "neural_prophet", "success": False, "error": str(e)}


def forecast_all(series: pd.Series, horizon: int = 12,
                  with_thi: bool = False,
                  with_neural: bool = False) -> list:
    """跑所有可用模型，回傳 list of result dicts。

    Args:
        with_thi: True 加入 sarima_thi 與 prophet_thi
        with_neural: True 加入 neural_prophet
    """
    results = []
    fns = [forecast_naive_seasonal, forecast_stl_linear,
           forecast_holt_winters, forecast_sarima, forecast_prophet]
    if with_thi:
        fns.extend([forecast_sarima_thi, forecast_prophet_thi])
    if with_neural:
        fns.append(forecast_neural_prophet)
    for fn in fns:
        r = fn(series, horizon=horizon)
        if r is not None:
            results.append(r)
    return results


def ensemble_forecast(results: list, weights: dict = None) -> dict:
    """簡單加權平均集成（只用成功的模型，且預設權重 = 1/in_sample_mape）。"""
    successful = [r for r in results if r.get("success") and r.get("forecast")]
    if not successful:
        return None

    if weights is None:
        # 倒數 MAPE 作權重
        ws = []
        for r in successful:
            mape = r.get("in_sample_mape", 10)
            ws.append(1.0 / max(mape, 1.0))
        total = sum(ws)
        weights = {r["model"]: w / total for r, w in zip(successful, ws)}

    months = sorted({pt["yyyymm"] for r in successful for pt in r["forecast"]})
    ensemble = []
    for m in months:
        p50_sum, p10_sum, p90_sum, w_sum = 0, 0, 0, 0
        for r in successful:
            w = weights.get(r["model"], 0)
            for pt in r["forecast"]:
                if pt["yyyymm"] == m:
                    p50_sum += w * pt["p50"]
                    p10_sum += w * pt["p10"]
                    p90_sum += w * pt["p90"]
                    w_sum += w
                    break
        if w_sum > 0:
            ensemble.append({
                "yyyymm": m,
                "p50": p50_sum / w_sum,
                "p10": p10_sum / w_sum,
                "p90": p90_sum / w_sum,
            })

    return {
        "model": "ensemble",
        "forecast": ensemble,
        "weights": weights,
        "success": True,
    }
