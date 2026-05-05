"""農業部統計處：歷年酪農場數、產乳牛頭數、產乳量。

來源：2-2畜牧生產113.ods（內含民國 104–113 年資料）

注意：此資料的「產乳量」是答案、不應該進預測；
用得上的是「產乳牛頭數」（在養量），可以做為獨立 scale factor 計算的輸入。
"""
from pathlib import Path
import pandas as pd
from .. import config


# 預先擷取（避免每次都解析 ods）
# 民國年 → 西元年 = 民國 + 1911
OFFICIAL_DAIRY_INVENTORY = {
    # year: {n_farms, n_milking_cows, milk_production_tons}
    2015: {"n_farms": 546, "n_milking_cows": 61859, "production_tons": 375498.835},
    2016: {"n_farms": 545, "n_milking_cows": 59601, "production_tons": 378488.421},
    2017: {"n_farms": 553, "n_milking_cows": 60523, "production_tons": 386361.874},
    2018: {"n_farms": 553, "n_milking_cows": 61967, "production_tons": 419341.805},
    2019: {"n_farms": 559, "n_milking_cows": 61813, "production_tons": 431879.283},
    2020: {"n_farms": 560, "n_milking_cows": 62916, "production_tons": 437154.578},
    2021: {"n_farms": 566, "n_milking_cows": 64974, "production_tons": 449214.217},
    2022: {"n_farms": 562, "n_milking_cows": 64516, "production_tons": 463094.868},
    2023: {"n_farms": 554, "n_milking_cows": 61681, "production_tons": 472449.164},
    2024: {"n_farms": 545, "n_milking_cows": 59259, "production_tons": 452413.589},
}


def get_milking_cows(year: int) -> int:
    """取得指定年的全國產乳牛頭數（全國總在養量）。"""
    info = OFFICIAL_DAIRY_INVENTORY.get(int(year))
    return info["n_milking_cows"] if info else None


def get_official_production(year: int) -> float:
    """取得指定年的全國產乳量（公噸）。注意這是答案、不應拿去預測。"""
    info = OFFICIAL_DAIRY_INVENTORY.get(int(year))
    return info["production_tons"] if info else None


def get_n_farms(year: int) -> int:
    """取得指定年的全國酪農場數（登記養乳牛場）。"""
    info = OFFICIAL_DAIRY_INVENTORY.get(int(year))
    return info["n_farms"] if info else None


def load_official_inventory(filepath: Path = None) -> dict:
    """重新從 ods 解析（資料有更新時用）。"""
    if filepath is None:
        filepath = config.ROOT / "raw_data" / "2-2畜牧生產113.ods"
    if not filepath.exists():
        return dict(OFFICIAL_DAIRY_INVENTORY)
    df = pd.read_excel(filepath, sheet_name="蛋類_牛乳_羊乳_養蜂_(2)",
                        engine="odf", header=None)
    # 從第 13 列開始是民國年數據（產乳牛場數=col 4, 產乳牛=col 5, 產乳量=col 6）
    out = {}
    for i in range(13, 30):
        row = df.iloc[i]
        # 民國年可能在 col 0 或 col 1
        roc = row.iloc[1] if pd.isna(row.iloc[0]) or not str(row.iloc[0]).startswith("民國") else row.iloc[1]
        if pd.isna(roc):
            continue
        try:
            roc_year = int(float(roc))
        except (ValueError, TypeError):
            continue
        if roc_year < 50 or roc_year > 130:
            continue
        ad_year = roc_year + 1911
        try:
            n_farms = int(row.iloc[4])
            n_cows = int(row.iloc[5])
            prod = float(row.iloc[6])
        except (ValueError, TypeError):
            continue
        out[ad_year] = {
            "n_farms": n_farms,
            "n_milking_cows": n_cows,
            "production_tons": prod,
        }
    return out
