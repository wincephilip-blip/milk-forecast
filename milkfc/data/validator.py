"""資料驗證層 - 在 pipeline 入口攔住格式或新鮮度異常。"""
import pandas as pd
from datetime import datetime, timedelta
from .. import config

class DataValidationError(Exception):
    pass

def validate_dhi(df: pd.DataFrame, ref_date: pd.Timestamp = None) -> dict:
    """
    Returns: dict with 'ok', 'warnings', 'errors', 'metrics'
    Raises DataValidationError if hard errors found.
    """
    ref_date = ref_date or pd.Timestamp.now().normalize()
    errors = []
    warnings = []
    metrics = {}

    # 1. 必要欄位
    missing_cols = [c for c in config.REQUIRED_COLUMNS if c not in df.columns]
    if missing_cols:
        errors.append(f"缺欄位: {missing_cols}")

    # 2. 紀錄筆數
    metrics["n_records"] = len(df)
    if len(df) < config.MIN_RECORDS_PER_FILE * 0.5:  # 整批不足一半年份
        warnings.append(f"紀錄量偏少 ({len(df):,}); 預期 ~{config.MIN_RECORDS_PER_FILE:,}/年")

    # 3. 欄位填寫率
    metrics["fill_rates"] = {}
    for col, min_rate in config.MIN_FILL_RATE.items():
        if col not in df.columns:
            continue
        rate = df[col].notna().mean()
        metrics["fill_rates"][col] = float(rate)
        if rate < min_rate:
            warnings.append(f"{col} 填寫率 {rate:.1%} 低於門檻 {min_rate:.0%}")

    # 4. 資料新鮮度
    if "sample_date" in df.columns:
        latest = df["sample_date"].max()
        metrics["latest_sample"] = str(latest.date()) if pd.notna(latest) else None
        if pd.notna(latest):
            age = (ref_date - latest).days
            metrics["data_age_days"] = age
            if age > config.DATA_FRESHNESS_DAYS:
                warnings.append(
                    f"最新紀錄 {latest.date()} 已 {age} 天 "
                    f"(門檻 {config.DATA_FRESHNESS_DAYS} 天)")

    # 5. ID 一致性 - farm_id / cow_id 不應有空
    if "farm_id" in df.columns:
        n_blank_farm = df["farm_id"].isin(["nan","None","",None]).sum()
        if n_blank_farm > 0:
            warnings.append(f"farm_id 異常空值 {n_blank_farm} 筆")
    if "cow_id" in df.columns:
        n_blank_cow = df["cow_id"].isin(["nan","None","",None]).sum()
        if n_blank_cow > 0:
            warnings.append(f"cow_id 異常空值 {n_blank_cow} 筆")

    # 6. 數值範圍
    if "milk_kg" in df.columns:
        out_of_range = ((df["milk_kg"] < 0) | (df["milk_kg"] > 100)).sum()
        if out_of_range > 0:
            warnings.append(f"乳量異常範圍 (<0 或 >100 kg/d): {out_of_range} 筆")
    if "parity" in df.columns:
        out = ((df["parity"] < 1) | (df["parity"] > 15)).sum()
        if out > 0:
            warnings.append(f"胎次異常 (<1 或 >15): {out} 筆")

    # 7. 場數
    if "farm_id" in df.columns:
        metrics["n_farms"] = int(df["farm_id"].nunique())
    if "cow_id" in df.columns:
        metrics["n_cows"] = int(df["cow_id"].nunique())

    if errors:
        raise DataValidationError("; ".join(errors))

    return {
        "ok": True,
        "warnings": warnings,
        "errors": errors,
        "metrics": metrics,
    }
