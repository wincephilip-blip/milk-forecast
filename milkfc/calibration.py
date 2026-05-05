"""涵蓋率校正：用官方季報把 DHI 加總外推到真實全國/縣市值。

核心邏輯：
  涵蓋率 = DHI 中該區域的乳牛頭數 / 官方該區域真實頭數
  真實估計 = DHI 加總 / 涵蓋率

時間動態版：每月各自算 scale factor，不再用單一固定值。
"""
import pandas as pd
import numpy as np
from typing import Optional
from .data import (get_county_stats, get_national_summary, load_farm_metadata,
                    parse_all_quarterly)


def compute_monthly_scale_factors(df_dhi: pd.DataFrame,
                                    months: list,
                                    forecast_horizon_months: int = 24) -> dict:
    """計算每月的 DHI → 全國 scale factor。

    用法：
      months = 預測涵蓋的所有月份（如 2024-01 ~ 2026-12）
      回傳 {yyyymm: scale_factor}

    邏輯：
      1. 從官方季報抓多季快照 → 內插或外推到月度
      2. 從 DHI 算每月活躍頭數
      3. scale_factor[m] = official[m] / DHI[m]

    若只有一份官方資料（如目前 114Q3）：
      → 對所有月份用同一個官方總數
      → 但 DHI 頭數還是逐月變動，所以 scale 仍然會變
    """
    # 1. 取得所有官方季報（時間序列）
    quarterly = parse_all_quarterly()

    # 2. 計算 DHI 月度活躍頭數
    df_dhi = df_dhi.copy()
    df_dhi["yyyymm"] = df_dhi["sample_date"].dt.to_period("M").astype(str)
    dhi_monthly_heads = df_dhi.groupby("yyyymm")["cow_id"].nunique().to_dict()

    # 3. 對每個目標月份算 scale factor
    result = {}
    for m in months:
        # DHI 該月活躍頭數
        dhi_h = dhi_monthly_heads.get(m, None)
        if dhi_h is None or dhi_h == 0:
            # 未來月份沒有 DHI 紀錄 → 用最近已知月份的頭數
            past_months = [k for k in dhi_monthly_heads if k < m]
            if past_months:
                dhi_h = dhi_monthly_heads[max(past_months)]
            else:
                continue

        # 官方該月對應頭數（線性內插或最近一季）
        target_date = pd.Timestamp(m + "-15")  # 用月中當代表
        if quarterly.empty:
            continue
        official_h = _interpolate_official(target_date, quarterly)
        if official_h is None:
            continue

        result[m] = {
            "dhi_heads": int(dhi_h),
            "official_milking": int(official_h),
            "rate": float(dhi_h / official_h) if official_h > 0 else 0,
            "scale_factor": float(official_h / dhi_h) if dhi_h > 0 else 1.0,
        }
    return result


def _interpolate_official(target_date, quarterly_df):
    """從多季快照線性內插出 target_date 的官方頭數，
    或外推（用最近趨勢）。"""
    if quarterly_df.empty:
        return None

    quarterly_df = quarterly_df.sort_values("period").reset_index(drop=True)

    if len(quarterly_df) == 1:
        # 只有一份資料 → 直接用該值（無趨勢可推）
        return quarterly_df["n_milking_cows"].iloc[0]

    # 找前後兩個最近的季點
    before = quarterly_df[quarterly_df["period"] <= target_date]
    after = quarterly_df[quarterly_df["period"] > target_date]

    if before.empty:
        # target 早於最早季 → 用最早值
        return after.iloc[0]["n_milking_cows"]
    if after.empty:
        # target 晚於最晚季 → 用最近兩點外推
        if len(quarterly_df) >= 2:
            p1, p2 = quarterly_df.iloc[-2], quarterly_df.iloc[-1]
            slope = (p2["n_milking_cows"] - p1["n_milking_cows"]) / \
                    ((p2["period"] - p1["period"]).days)
            days_after = (target_date - p2["period"]).days
            extrapolated = p2["n_milking_cows"] + slope * days_after
            return max(extrapolated, p2["n_milking_cows"] * 0.7)  # 防爆跌
        return before.iloc[-1]["n_milking_cows"]

    # 線性內插
    p1 = before.iloc[-1]
    p2 = after.iloc[0]
    span_days = (p2["period"] - p1["period"]).days
    if span_days == 0:
        return p1["n_milking_cows"]
    weight = (target_date - p1["period"]).days / span_days
    return p1["n_milking_cows"] + weight * (p2["n_milking_cows"] - p1["n_milking_cows"])


# 縣市名標準化（處理「臺/台」、「縣/市」差異）
COUNTY_NORMALIZE = {
    "台北市":"臺北市", "台中市":"臺中市", "台南市":"臺南市", "台東縣":"臺東縣",
    "台南":"臺南市", "台北":"臺北市", "台中":"臺中市", "台東":"臺東縣",
    "臺南":"臺南市", "臺北":"臺北市", "臺中":"臺中市", "臺東":"臺東縣",
}


def _normalize_county(name):
    if pd.isna(name): return None
    s = str(name).strip()
    return COUNTY_NORMALIZE.get(s, s)


def map_farm_to_county(df: pd.DataFrame,
                       farm_meta: Optional[pd.DataFrame] = None) -> dict:
    """根據 Farm.xlsx 的郵遞區號/區域對應每場到縣市。

    Returns: {farm_id: county_name}
    """
    if farm_meta is None:
        farm_meta = load_farm_metadata()
    if farm_meta.empty:
        return {}

    # 簡化作法：用 region_name 直接（如「彰化」、「屏東-萬丹」）
    # 取第一段作為縣市，沒有的話用郵遞區號粗略對應
    POSTAL_TO_COUNTY = {
        # 主要乳牛產區的郵遞區號前 3 碼
        "913":"屏東縣","912":"屏東縣","920":"屏東縣","909":"屏東縣",
        "905":"屏東縣","908":"屏東縣","907":"屏東縣",
        "915":"屏東縣","906":"屏東縣",
        "915":"屏東縣","916":"屏東縣","911":"屏東縣",
        "736":"臺南市","737":"臺南市","735":"臺南市","734":"臺南市","733":"臺南市",
        "732":"臺南市","739":"臺南市","720":"臺南市",
        "506":"彰化縣","505":"彰化縣","508":"彰化縣","515":"彰化縣","520":"彰化縣",
        "500":"彰化縣","510":"彰化縣","521":"彰化縣","527":"彰化縣","528":"彰化縣",
        "522":"彰化縣","523":"彰化縣","524":"彰化縣","525":"彰化縣","526":"彰化縣",
        "615":"雲林縣","630":"雲林縣","631":"雲林縣","633":"雲林縣","637":"雲林縣",
        "638":"雲林縣","640":"雲林縣","647":"雲林縣","648":"雲林縣","649":"雲林縣",
        "651":"雲林縣","652":"雲林縣","653":"雲林縣",
        "604":"嘉義縣","605":"嘉義縣","608":"嘉義縣","612":"嘉義縣","614":"嘉義縣",
        "616":"嘉義縣","622":"嘉義縣","625":"嘉義縣",
        "815":"高雄市","845":"高雄市","832":"高雄市","830":"高雄市","843":"高雄市",
        "326":"桃園市","327":"桃園市","328":"桃園市","330":"桃園市","333":"桃園市",
        "335":"桃園市","338":"桃園市","350":"桃園市",
        "361":"苗栗縣","362":"苗栗縣","363":"苗栗縣","364":"苗栗縣","367":"苗栗縣",
        "368":"苗栗縣","369":"苗栗縣",
        "975":"花蓮縣","978":"花蓮縣","981":"花蓮縣","982":"花蓮縣","983":"花蓮縣",
        "950":"臺東縣","951":"臺東縣","952":"臺東縣","953":"臺東縣","954":"臺東縣",
    }

    mapping = {}
    for _, r in farm_meta.iterrows():
        fid = str(r["farm_id"])
        # 優先用郵遞區號
        p3 = str(r.get("postal_3","")).strip()
        if p3 in POSTAL_TO_COUNTY:
            mapping[fid] = POSTAL_TO_COUNTY[p3]
        else:
            # 否則用 region_name 抓「-」前段
            rn = str(r.get("region_name","")).split("-")[0].strip()
            if rn and rn != "其他":
                # 先標準化（台→臺）
                rn_n = COUNTY_NORMALIZE.get(rn, rn)
                # 如果已經是完整縣市名，直接用
                if rn_n.endswith(("縣","市")):
                    mapping[fid] = rn_n
                # 補上「縣/市」
                elif rn_n in ["彰化","雲林","嘉義","屏東","臺東","花蓮","南投",
                              "新竹","苗栗","宜蘭","澎湖","金門","連江"]:
                    mapping[fid] = rn_n + "縣"
                elif rn_n in ["臺北","臺南","臺中","桃園","高雄","新北","基隆"]:
                    mapping[fid] = rn_n + "市"
                else:
                    mapping[fid] = rn_n
    return mapping


def compute_coverage_rates(df_dhi: pd.DataFrame,
                            farm_meta: Optional[pd.DataFrame] = None,
                            ref_date: Optional[pd.Timestamp] = None) -> dict:
    """計算 DHI 在各縣市/全國的涵蓋率。

    Returns:
        {
            "national": {"dhi_heads": int, "official_heads": int, "rate": float},
            "by_county": {county: {dhi_heads, official_heads, rate}},
            "by_macro": {macro_region: {...}},
            "scale_factor_national": float (= 1/rate),
            "scale_factor_by_county": {county: float},
            "scale_factor_by_macro": {macro: float},
        }
    """
    # 取最近的 DHI 資料估算頭數
    if ref_date is None:
        ref_date = df_dhi["sample_date"].max()
    recent_cutoff = ref_date - pd.Timedelta(days=180)
    recent = df_dhi[df_dhi["sample_date"] > recent_cutoff]

    # 每場活躍頭數 = 該場最近 180 天唯一牛數
    farm_heads = recent.groupby("farm_id")["cow_id"].nunique().to_dict()

    # 每場縣市對應
    farm_county = map_farm_to_county(df_dhi, farm_meta)

    # 官方縣市資料
    official = get_county_stats()
    official_dict = dict(zip(official["county"], official["n_heads_total"]))
    macro_dict = official.groupby("macro_region")["n_heads_total"].sum().to_dict()
    summary = get_national_summary()

    # DHI 加總
    dhi_total = sum(farm_heads.values())

    # 按縣市
    dhi_by_county = {}
    dhi_by_macro = {}
    from .data.national_stats import COUNTY_TO_MACRO
    for fid, n in farm_heads.items():
        county = farm_county.get(str(fid))
        if county:
            dhi_by_county[county] = dhi_by_county.get(county, 0) + n
            macro = COUNTY_TO_MACRO.get(county, "其他")
            dhi_by_macro[macro] = dhi_by_macro.get(macro, 0) + n

    # 用產乳牛當對照（DHI 紀錄的就是泌乳期的牛，更合理的對照）
    official_milking_dict = dict(zip(
        official["county"], official["n_milking_cows"]))

    # 涵蓋率
    by_county = {}
    sf_county = {}
    for county, dhi_n in dhi_by_county.items():
        # 標準化縣市名（處理 台/臺）
        norm = _normalize_county(county)
        off_n = official_dict.get(norm, official_dict.get(county, 0))
        off_milk = official_milking_dict.get(norm,
                                              official_milking_dict.get(county, 0))
        rate = dhi_n / off_milk if off_milk > 0 else 0
        by_county[norm or county] = {
            "dhi_heads": int(dhi_n),
            "official_heads": int(off_n),
            "official_milking": int(off_milk),
            "rate": float(rate),
        }
        sf_county[norm or county] = (1.0 / rate) if rate > 0 else 1.0

    macro_milk_dict = official.groupby("macro_region")["n_milking_cows"].sum().to_dict()
    by_macro = {}
    sf_macro = {}
    for macro, dhi_n in dhi_by_macro.items():
        off_n = macro_dict.get(macro, 0)
        off_milk = macro_milk_dict.get(macro, 0)
        rate = dhi_n / off_milk if off_milk > 0 else 0
        by_macro[macro] = {
            "dhi_heads": int(dhi_n),
            "official_heads": int(off_n),
            "official_milking": int(off_milk),
            "rate": float(rate),
        }
        sf_macro[macro] = (1.0 / rate) if rate > 0 else 1.0

    # 全國涵蓋率以「產乳牛」為基準（更合理的比較）
    nat_rate = dhi_total / summary["n_milking_cows"] if summary else 0
    return {
        "national": {
            "dhi_heads": int(dhi_total),
            "official_heads": summary.get("n_heads_total", 0),
            "official_milking": summary.get("n_milking_cows", 0),
            "official_farms": summary.get("n_farms", 0),
            "official_period": summary.get("period", "?"),
            "rate": float(nat_rate),
        },
        "by_county": by_county,
        "by_macro": by_macro,
        "scale_factor_national": (1.0 / nat_rate) if nat_rate > 0 else 1.0,
        "scale_factor_by_county": sf_county,
        "scale_factor_by_macro": sf_macro,
        "farm_to_county": farm_county,
    }


def aggregate_by_region(forecasts_with_farm: pd.DataFrame,
                         farm_to_county: dict,
                         scope: str = "macro") -> pd.DataFrame:
    """按區域加總場別預測結果。

    Args:
        forecasts_with_farm: 含 farm_id 與 yyyymm/p10/p50/p90 欄位
        farm_to_county: {farm_id: county_name}
        scope: "national" / "macro" / "county"
    Returns:
        DataFrame [region, yyyymm, p10, p50, p90, actual]
    """
    df = forecasts_with_farm.copy()
    df["farm_id"] = df["farm_id"].astype(str)

    if scope == "national":
        df["region"] = "全國"
    elif scope == "macro":
        from .data.national_stats import COUNTY_TO_MACRO
        df["county"] = df["farm_id"].map(farm_to_county)
        df["region"] = df["county"].map(COUNTY_TO_MACRO).fillna("其他")
    else:  # county
        df["region"] = df["farm_id"].map(farm_to_county).fillna("未知")

    cols = {"p10":("p10","sum"), "p50":("p50","sum"), "p90":("p90","sum")}
    if "actual" in df.columns:
        # 用 min_count=1 保留 NaN
        agg = df.groupby(["region","yyyymm"]).agg(**cols).reset_index()
        actual_agg = df.dropna(subset=["actual"]).groupby(
            ["region","yyyymm"])["actual"].sum().reset_index(name="actual")
        agg = agg.merge(actual_agg, on=["region","yyyymm"], how="left")
    else:
        agg = df.groupby(["region","yyyymm"]).agg(**cols).reset_index()
    return agg
