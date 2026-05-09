"""一次性腳本：從 DHI raw cache 抽取月度 Q（avg kg/day per record）。

輸出：snapshots/_dhi_monthly_yield.json
格式：{"2019-01": 24.85, "2019-02": 25.12, ...}
"""
import sys
import json
import pickle
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

import pandas as pd
from milkfc import config

CACHE = config.SNAPSHOT_DIR / "_cache.pkl"
OUT = config.SNAPSHOT_DIR / "_dhi_monthly_yield.json"

print(f"Loading {CACHE} ...")
with open(CACHE, "rb") as f:
    df = pickle.load(f)
print(f"Loaded {len(df):,} DHI records")

# 過濾必要欄位
df = df[df["sample_date"].notna() & df["milk_kg"].notna()].copy()
df["yyyymm"] = pd.to_datetime(df["sample_date"]).dt.to_period("M").astype(str)

# 月度平均 milk_kg/record（= 每頭每測試日平均擠乳量 kg/day）
monthly = df.groupby("yyyymm").agg(
    avg_kg_per_record=("milk_kg", "mean"),
    n_records=("milk_kg", "count"),
    n_farms=("farm_id", "nunique"),
).round(4)

# 過濾樣本量不足之早期月份（與 timeseries 模組同準則）
monthly = monthly[(monthly["n_records"] >= 100) & (monthly["n_farms"] >= 30)]
monthly = monthly.sort_index()

print(f"Monthly Q series: {len(monthly)} months "
      f"({monthly.index[0]} ~ {monthly.index[-1]})")
print("Sample (last 5):")
print(monthly.tail(5))

# 輸出
out_dict = {
    yyyymm: {
        "Q_kg_per_day": float(row["avg_kg_per_record"]),
        "n_records": int(row["n_records"]),
        "n_farms": int(row["n_farms"]),
    }
    for yyyymm, row in monthly.iterrows()
}
OUT.write_text(json.dumps(out_dict, ensure_ascii=False, indent=2))
print(f"Saved: {OUT} ({len(out_dict)} months)")
