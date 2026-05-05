"""異常場偵測 - 連續 N 月偏差超門檻時告警."""
import pandas as pd
from .. import config

def detect_anomalies(forecast_with_actual: pd.DataFrame,
                     bias_threshold: float = None,
                     consec_months: int = None) -> dict:
    """
    Inputs: forecast_with_actual 需有 yyyymm, p50, actual 三欄。
    Returns: {
        'is_anomalous': bool,
        'severity': 'normal' / 'warning' / 'alert',
        'consecutive_breach_months': int,
        'breach_months': list of {month, err_pct},
        'message': str,
    }
    """
    bias_thr = bias_threshold or config.ANOMALY_BIAS_THRESHOLD
    consec = consec_months or config.ANOMALY_CONSEC_MONTHS

    df = forecast_with_actual.copy()
    df = df[df["actual"].notna()].sort_values("yyyymm")
    if len(df) == 0:
        return {"is_anomalous": False, "severity": "normal",
                "consecutive_breach_months": 0, "breach_months": [],
                "message": "無實際資料可比"}

    df["err_pct"] = (df["p50"] - df["actual"]) / df["actual"] * 100
    df["breach"] = df["err_pct"].abs() > bias_thr

    # 找最長連續超標
    max_run = 0
    cur_run = 0
    breach_months = []
    for _, r in df.iterrows():
        if r["breach"]:
            cur_run += 1
            max_run = max(max_run, cur_run)
            breach_months.append({"month": r["yyyymm"],
                                   "err_pct": float(r["err_pct"])})
        else:
            cur_run = 0

    is_anom = max_run >= consec
    if max_run == 0:
        sev = "normal"
        msg = "無超標月份"
    elif max_run < consec:
        sev = "warning"
        msg = f"單月偏差超門檻（最長 {max_run} 月）但未達連續 {consec} 月觸發"
    else:
        sev = "alert"
        msg = (f"連續 {max_run} 月偏差 > ±{bias_thr}%，建議人工審閱"
               f"（可能為管理變動、疫情、或資料品質問題）")

    return {
        "is_anomalous": is_anom,
        "severity": sev,
        "consecutive_breach_months": int(max_run),
        "breach_months": breach_months,
        "message": msg,
        "threshold_pct": float(bias_thr),
        "total_months_evaluated": len(df),
    }
