"""載入 Farm.xlsx 場別後設資料 + 區域代碼對應."""
import pandas as pd
import openpyxl
from datetime import datetime
from pathlib import Path
from .. import config

# 地區代號 → 區域 對應（依台灣縣市/酪農聯誼會分區慣例）
# 1000-1900 為台灣主要酪農區
REGION_MAP = {
    500: "彰化", 600: "雲林", 700: "嘉義", 800: "台南",
    1000: "屏東", 1100: "台東", 1200: "台南-柳營", 1300: "雲林-崙背",
    1400: "屏東-萬丹", 1600: "高雄", 1800: "彰化-福興",
    1900: "苗栗", 2000: "桃園", 200: "新竹", 244: "苗栗",
}

# 區域 → 大區域（北/中/南/東）
MACRO_REGION = {
    "新竹":"北", "桃園":"北", "苗栗":"中",
    "彰化":"中", "彰化-福興":"中", "雲林":"中", "雲林-崙背":"中",
    "嘉義":"南", "台南":"南", "台南-柳營":"南",
    "高雄":"南", "屏東":"南", "屏東-萬丹":"南",
    "台東":"東",
}


def load_farm_metadata() -> pd.DataFrame:
    """載入 Farm.xlsx，回傳 farm_id (str) → metadata 對應表。"""
    fp = config.RAW_DIR / "Farm.xlsx"
    if not fp.exists():
        return pd.DataFrame()

    df = pd.read_excel(fp, sheet_name="cowowner", engine="openpyxl")

    # 標準欄位
    df = df.rename(columns={
        "酪農代號": "farm_id",
        "酪農姓名": "farmer_name",
        "牧場名稱": "farm_name",
        "輔導員編號": "advisor_id",
        "郵遞區號": "postal_code",
        "地址": "address",
        "成立年月": "establishment",
        "地區代號": "region_code",
    })
    df["farm_id"] = df["farm_id"].astype(str)

    # 解析成立年月（民國年）
    def parse_establishment(s):
        if pd.isna(s): return None
        s = str(s).strip()
        try:
            if len(s) >= 5:
                roc_year = int(s[:-2])
                month = int(s[-2:])
                return datetime(roc_year + 1911, month, 1)
            elif len(s) == 4:
                roc_year = int(s[:-2])
                month = int(s[-2:])
                return datetime(roc_year + 1911, month, 1)
        except (ValueError, IndexError):
            pass
        return None

    df["establishment_date"] = df["establishment"].apply(parse_establishment)
    df["age_years"] = df["establishment_date"].apply(
        lambda d: (datetime.now() - d).days / 365.25 if d else None)

    # 區域對應
    df["region_name"] = df["region_code"].map(
        lambda c: REGION_MAP.get(int(c), "其他") if pd.notna(c) else "其他")
    df["macro_region"] = df["region_name"].map(MACRO_REGION).fillna("其他")

    # 縣市（從郵遞區號前 3 碼）
    df["postal_3"] = df["postal_code"].astype(str).str[:3]

    return df[["farm_id", "farmer_name", "farm_name",
               "advisor_id", "postal_code", "postal_3",
               "establishment_date", "age_years",
               "region_code", "region_name", "macro_region"]]
