"""性控精液偵測 - 從 NAAB 代碼前綴判斷."""
import pandas as pd
import numpy as np

# NAAB 標準性控精液代碼前綴
# 規則: 一般精液 NAAB code + 500 = 性控版本（多數公司）
#       Alta Genetics: 200 → 250
#       特殊代碼: 614, 629, 664 (Trans-Ova, Genus, others)
SEXED_PREFIXES = {
    # +500 family
    "501",  # ABS Global sexed
    "507",  # Select Sires sexed
    "511",  # Genex sexed
    "514",  # Quebec sexed
    "515",  # Quebec sexed (variant)
    "523",  # Cogent sexed
    "529",  # Genus sexed
    # Alta Genetics sexed
    "250",
    "251",
    # 其他公司性控
    "614",  # Trans-Ova sexed
    "629",  # Genus sexed (variant)
    "664",  # Other sexed
    "709",  # Some sexed lines
}

# 母犢比例（標準假設）
FEMALE_RATIO_CONVENTIONAL = 0.50   # 一般精液約 50% 母犢
FEMALE_RATIO_SEXED = 0.87          # 性控精液約 87% 母犢


def is_sexed(semen_code) -> bool:
    """判斷單一精液代碼是否為性控."""
    if not semen_code or pd.isna(semen_code):
        return False
    s = str(semen_code).strip().upper()
    if len(s) < 3:
        return False
    return s[:3] in SEXED_PREFIXES


def compute_farm_sexed_rate(df: pd.DataFrame) -> dict:
    """每場最後 12 個月的性控精液使用比例。

    Returns:
        {farm_id: sexed_rate (0.0 - 1.0)}
    """
    if "last_breeding_date" not in df.columns:
        return {}
    train_end = df["sample_date"].max()
    cutoff = train_end - pd.Timedelta(days=365)
    recent = df[(df["last_breeding_date"] > cutoff) &
                df["last_breeding_semen"].notna()].drop_duplicates(
        ["cow_id","last_breeding_date"])
    recent = recent.copy()
    # 顯式轉 float 避免新 pandas 把 bool 推成 StringDtype
    recent["is_sexed"] = recent["last_breeding_semen"].apply(is_sexed).astype(float)
    return recent.groupby("farm_id")["is_sexed"].mean().to_dict()


def adjusted_female_rate(sexed_rate: float) -> float:
    """考量性控比例後的母犢比例。

    Args:
        sexed_rate: 該場最近性控精液使用比例 (0.0-1.0)
    Returns:
        加權母犢比例 (0.50-0.87)
    """
    return (FEMALE_RATIO_CONVENTIONAL * (1 - sexed_rate) +
            FEMALE_RATIO_SEXED * sexed_rate)


def adjust_heifer_rate(base_heifer_rate: float, sexed_rate: float,
                        base_assumption: float = FEMALE_RATIO_CONVENTIONAL) -> float:
    """根據實際性控比例調整後備母牛入場率。

    Args:
        base_heifer_rate: 原本估的入場率（隱含 50% 母犢假設）
        sexed_rate: 該場性控精液比例
        base_assumption: 原本估算時假設的母犢比例（預設 0.5）
    Returns:
        調整後的入場率
    """
    actual_female = adjusted_female_rate(sexed_rate)
    return base_heifer_rate * (actual_female / base_assumption)
