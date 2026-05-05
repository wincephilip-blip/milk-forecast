"""中央氣象署 (CWA) Open Data 整合模組。

API: https://opendata.cwa.gov.tw
資料集:
  C-B0027-001: 月氣候平均統計（30 年常態值，含氣溫/濕度/降雨/風速）
  O-A0003-001: 自動氣象站逐時觀測

使用方式:
  export CWA_API_KEY="CWA-XXXXX..."
  from milkfc.data.weather import fetch_climate_normals, compute_thi
"""
import os
import json
import math
import urllib.request
import urllib.parse
from pathlib import Path
from typing import Optional
import pandas as pd

CWA_BASE = "https://opendata.cwa.gov.tw/api/v1/rest/datastore"

# 縣市 → 最近的 CWA 署屬有人氣象站
# 註：彰化/雲林/桃園/苗栗無直屬站，用相鄰縣市的站當代表
FARM_COUNTY_TO_STATIONS = {
    # 北部
    "基隆市":   ["466940"],   # 基隆
    "臺北市":   ["466920"],   # 臺北
    "新北市":   ["466880", "466900"],  # 板橋、淡水
    "桃園市":   ["466880"],   # 板橋（最近）
    "新竹縣":   ["467571"],   # 新竹
    "新竹市":   ["467571"],
    "苗栗縣":   ["467571", "467490"],  # 新竹/臺中
    "宜蘭縣":   ["467080", "467060"],  # 宜蘭、蘇澳
    # 中部
    "臺中市":   ["467490", "467770"],  # 臺中、梧棲
    "彰化縣":   ["467490", "467770"],  # 用臺中/梧棲代表
    "南投縣":   ["467650", "467530"],  # 日月潭、阿里山
    "雲林縣":   ["467480", "467770"],  # 嘉義/梧棲代表
    # 南部
    "嘉義縣":   ["467480"],   # 嘉義
    "嘉義市":   ["467480"],
    "臺南市":   ["467410", "467420"],  # 臺南、永康
    "高雄市":   ["467440"],   # 高雄
    "屏東縣":   ["467590", "467540"],  # 恆春、大武
    # 東部
    "花蓮縣":   ["466990"],   # 花蓮
    "臺東縣":   ["467660", "467610"],  # 臺東、成功
    # 離島
    "澎湖縣":   ["467350", "467300"],  # 澎湖、東吉島
}

CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "snapshots" / "weather_cache"


def _api_key() -> str:
    """從環境變數讀 CWA API 金鑰。"""
    key = os.environ.get("CWA_API_KEY")
    if not key:
        raise RuntimeError(
            "CWA_API_KEY 未設定。請執行：\n"
            "  export CWA_API_KEY='CWA-XXXXX...'\n"
            "或加進 ~/.zshrc"
        )
    return key


def _fetch_json(dataset_id: str, **params) -> dict:
    """呼叫 CWA API（含 SSL 容錯）。"""
    import ssl
    params["Authorization"] = _api_key()
    params["format"] = "JSON"
    qs = urllib.parse.urlencode(params)
    url = f"{CWA_BASE}/{dataset_id}?{qs}"
    # CWA 憑證在某些 macOS Python 會 SSL Subject Key Identifier 錯、
    # 直接用不驗證 host name 的 SSL context（公開 API、僅讀取）
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(url, timeout=30, context=ctx) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_climate_normals(use_cache: bool = True) -> pd.DataFrame:
    """抓 30 年月氣候常態 (C-B0027-001)。

    Returns:
        DataFrame columns:
          station_id, station_name, county, month,
          tmean, tmax, tmin, rh_mean, rainfall, sunshine_hr, wind_speed
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / "climate_normals.pkl"
    if use_cache and cache_file.exists():
        return pd.read_pickle(cache_file)

    data = _fetch_json("C-B0027-001")
    records = []
    locations = data["records"]["data"]["surfaceObs"]["location"]
    for loc in locations:
        st = loc["station"]
        sid = st["StationID"]
        sname = st["StationName"]
        stats = loc["stationObsStatistics"]

        # 氣溫
        for entry in stats.get("AirTemperature", {}).get("monthly", []):
            month = int(entry["Month"])
            r = {
                "station_id": sid, "station_name": sname, "month": month,
                "tmean": float(entry.get("Mean", 0) or 0),
                "tmax_avg": float(entry.get("Maximum", 0) or 0),
                "tmin_avg": float(entry.get("Minimum", 0) or 0),
                "days_max_ge_30": float(entry.get("maxGE30Days", 0) or 0),
                "days_mean_ge_25": float(entry.get("meanGE25Days", 0) or 0),
                "days_min_le_10": float(entry.get("minLE10Days", 0) or 0),
            }
            records.append(r)

    df_temp = pd.DataFrame(records)

    # 濕度（同樣方式併進來）
    rh_records = []
    for loc in locations:
        sid = loc["station"]["StationID"]
        for entry in loc["stationObsStatistics"].get("RelativeHumidity", {}).get("monthly", []):
            rh_records.append({
                "station_id": sid, "month": int(entry["Month"]),
                "rh_mean": float(entry.get("Mean", 0) or 0),
            })
    df_rh = pd.DataFrame(rh_records)

    # 降雨
    rain_records = []
    for loc in locations:
        sid = loc["station"]["StationID"]
        for entry in loc["stationObsStatistics"].get("Precipitation", {}).get("monthly", []):
            rain_records.append({
                "station_id": sid, "month": int(entry["Month"]),
                "rainfall_mm": float(entry.get("Accumulation", 0) or 0),
                "rainy_days": float(entry.get("GE01Days", 0) or 0),
            })
    df_rain = pd.DataFrame(rain_records)

    df = df_temp.merge(df_rh, on=["station_id","month"], how="left")
    df = df.merge(df_rain, on=["station_id","month"], how="left")

    # 計算 THI
    df["thi"] = df.apply(lambda r: compute_thi(r["tmean"], r["rh_mean"]), axis=1)
    df["thi_max"] = df.apply(lambda r: compute_thi(r["tmax_avg"], r["rh_mean"]), axis=1)
    df["heat_stress_severity"] = df["thi"].apply(_thi_to_stress)

    # 對應到縣市
    df["county"] = df["station_id"].apply(_station_to_county)

    if use_cache:
        df.to_pickle(cache_file)
    return df


def compute_thi(temp_c: float, rh_pct: float) -> float:
    """Temperature-Humidity Index (NRC 1971 標準).

    THI = 1.8T + 32 - (0.55 - 0.0055 RH) (1.8T - 26)
    """
    if temp_c is None or rh_pct is None:
        return None
    if temp_c == 0 and rh_pct == 0:
        return None
    t_f = 1.8 * temp_c + 32
    return t_f - (0.55 - 0.0055 * rh_pct) * (1.8 * temp_c - 26)


def _thi_to_stress(thi: float) -> str:
    """乳牛 THI 熱緊迫等級分類（NRC / Armstrong 1994）"""
    if thi is None: return "unknown"
    if thi < 68: return "comfortable"
    if thi < 72: return "mild"
    if thi < 80: return "moderate"
    if thi < 90: return "severe"
    return "extreme"


def _station_to_county(station_id: str) -> str:
    for county, stations in FARM_COUNTY_TO_STATIONS.items():
        if station_id in stations:
            return county
    return "其他"


def get_county_thi_baseline(county: str) -> dict:
    """回傳該縣市每月 THI 基準值（從 30 年氣候常態算）。

    Returns:
        {1: thi_jan, 2: thi_feb, ..., 12: thi_dec}
    """
    df = fetch_climate_normals()
    sub = df[df["county"] == county]
    if sub.empty:
        return {}
    return dict(zip(sub["month"], sub["thi"]))


def heat_stress_factor(thi: float, threshold: float = 72.0,
                        slope: float = 0.005) -> float:
    """把 THI 轉成乳量修正係數。

    THI <= threshold (72): 無影響、回傳 1.0
    THI > threshold: 線性懲罰，每超過 1 度 THI 扣 0.5%
    例如 THI = 80 → factor = 1 - 0.005 * (80-72) = 0.96 (扣 4%)
    """
    if thi is None or thi <= threshold:
        return 1.0
    excess = thi - threshold
    return max(0.5, 1.0 - slope * excess)


def thi_predicted_seasonal(county: str) -> dict:
    """從 THI 30 年常態推算「物理上預期的季節乘子」（已標準化、月均=1）。

    這是先驗：在沒看資料前，根據氣象推測該縣市「該月該多少 vs 全年平均」。
    """
    thi = get_county_thi_baseline(county)
    if not thi:
        return {m: 1.0 for m in range(1, 13)}
    factors = {m: heat_stress_factor(t) for m, t in thi.items()}
    avg = sum(factors.values()) / len(factors)
    if avg == 0:
        return {m: 1.0 for m in range(1, 13)}
    return {m: factors.get(m, avg) / avg for m in range(1, 13)}


def fetch_monthly_historical_observations(use_cache: bool = True) -> pd.DataFrame:
    """抓 CWA 各站歷史月度氣候資料（用於 THI 計算）。

    嘗試 dataset C-B0024-001（局署屬氣象站累年累月觀測資料），
    解析後合併成 (station_id, year, month, tmean, rh_mean, thi) DataFrame。

    Returns:
        DataFrame，可能為空（如 API 失敗或資料集格式變動）。
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / "monthly_observations.pkl"
    if use_cache and cache_file.exists():
        try:
            return pd.read_pickle(cache_file)
        except Exception:
            pass

    try:
        data = _fetch_json("C-B0024-001")
    except Exception as e:
        print(f"[warn] CWA C-B0024-001 抓取失敗: {e}")
        return pd.DataFrame()

    records = []
    try:
        locations = data["records"]["data"]["surfaceObs"]["location"]
    except Exception:
        locations = data.get("records", {}).get("data", {}).get("location", [])
    for loc in locations:
        st = loc.get("station", {})
        sid = st.get("StationID", "")
        # 解析逐年逐月觀測（dataset 結構可能因 CWA 改版有差）
        years = (loc.get("stationObsStatistics", {})
                    .get("AirTemperature", {}).get("yearMonth", []))
        for entry in years:
            try:
                y = int(entry.get("Year", 0))
                m = int(entry.get("Month", 0))
                tmean = float(entry.get("Mean", 0) or 0)
            except (ValueError, TypeError):
                continue
            if y > 1990 and 1 <= m <= 12 and tmean != 0:
                records.append({"station_id": sid, "year": y,
                                  "month": m, "tmean": tmean})

    if not records:
        print("[warn] CWA C-B0024-001 沒解析到歷史月度資料、可能格式已變、退回 fallback")
        return pd.DataFrame()

    df_t = pd.DataFrame(records)

    # 同方法抓濕度
    rh_records = []
    for loc in locations:
        sid = loc.get("station", {}).get("StationID", "")
        years = (loc.get("stationObsStatistics", {})
                    .get("RelativeHumidity", {}).get("yearMonth", []))
        for entry in years:
            try:
                y = int(entry.get("Year", 0))
                m = int(entry.get("Month", 0))
                rh = float(entry.get("Mean", 0) or 0)
            except (ValueError, TypeError):
                continue
            if y > 1990 and 1 <= m <= 12 and rh > 0:
                rh_records.append({"station_id": sid, "year": y,
                                    "month": m, "rh_mean": rh})
    df_rh = pd.DataFrame(rh_records) if rh_records else pd.DataFrame(
        columns=["station_id","year","month","rh_mean"])

    df = df_t.merge(df_rh, on=["station_id","year","month"], how="left")
    df["rh_mean"] = df["rh_mean"].fillna(75.0)
    df["thi"] = df.apply(lambda r: compute_thi(r["tmean"], r["rh_mean"]), axis=1)
    df["county"] = df["station_id"].apply(_station_to_county)

    if use_cache:
        df.to_pickle(cache_file)
    return df


def load_monthly_thi_from_csv(csv_path: Path) -> pd.DataFrame:
    """讀使用者自備的月度 THI 資料（手動從 CWA 入口下載 CSV）。

    CSV 必要欄位：station_id 或 county、year、month、tmean、rh_mean
    （或直接含 thi 欄位）。
    """
    df = pd.read_csv(csv_path)
    if "thi" not in df.columns:
        df["thi"] = df.apply(lambda r: compute_thi(r["tmean"], r["rh_mean"]),
                                axis=1)
    if "county" not in df.columns and "station_id" in df.columns:
        df["county"] = df["station_id"].apply(_station_to_county)
    cache_file = CACHE_DIR / "monthly_observations.pkl"
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    df.to_pickle(cache_file)
    return df


def get_national_thi_monthly(target_months: list = None,
                                weights: dict = None,
                                use_history: bool = True) -> "pd.Series":
    """全國月度 THI 序列（縣市產乳牛加權平均）。

    Args:
        target_months: 月份清單，例 ['2020-01', '2020-02', ...]；
            None → 回傳 12 月 climate normal
        weights: {county: weight}；None 用台灣主要乳牛縣市分布
        use_history: True 時、優先用實測歷史資料（從 fetch_monthly_historical_observations）；
            未在歷史中的月份退回 climate normal

    Returns:
        pd.Series indexed by 'YYYY-MM'（或 1-12）、值為月度 THI
    """
    if weights is None:
        # 台灣產乳牛主要縣市權重（依 2024 年度資料、加總 ~95%）
        weights = {
            "彰化縣": 25, "雲林縣": 17, "屏東縣": 17,
            "臺南市": 16, "嘉義縣": 8, "桃園市": 5,
            "高雄市": 5, "苗栗縣": 2,
        }

    # fallback：台灣典型月 THI（避免 CWA API 失敗）
    typical = {1: 60, 2: 62, 3: 66, 4: 71, 5: 75, 6: 78,
                7: 80, 8: 80, 9: 77, 10: 72, 11: 67, 12: 62}

    monthly = None
    try:
        county_thi = {}
        for county in weights:
            baseline = get_county_thi_baseline(county)
            if baseline:
                county_thi[county] = baseline
        if county_thi:
            agg = {m: 0.0 for m in range(1, 13)}
            total_w = 0.0
            for county, baseline in county_thi.items():
                w = weights.get(county, 0)
                if w <= 0:
                    continue
                for m, t in baseline.items():
                    agg[m] += t * w
                total_w += w
            if total_w > 0:
                monthly = {m: v / total_w for m, v in agg.items()}
    except Exception:
        pass

    if monthly is None:
        monthly = typical

    if target_months is None:
        return pd.Series(monthly)

    # === 優先用實測歷史 THI、無資料的月份退回 climate normal ===
    history_lookup = {}
    if use_history:
        try:
            df_hist = fetch_monthly_historical_observations()
            if not df_hist.empty and "year" in df_hist.columns:
                # 縣市權重加權平均出全國月 THI（year, month）→ thi
                # 過濾只留主要乳牛縣市
                df_h = df_hist[df_hist["county"].isin(weights.keys())].copy()
                if not df_h.empty:
                    df_h["w"] = df_h["county"].map(weights)
                    grp = df_h.groupby(["year","month"]).apply(
                        lambda g: (g["thi"] * g["w"]).sum() / g["w"].sum()
                                    if g["w"].sum() > 0 else None
                    ).reset_index(name="thi")
                    for _, row in grp.iterrows():
                        if row["thi"] is not None:
                            ym = f"{int(row['year'])}-{int(row['month']):02d}"
                            history_lookup[ym] = float(row["thi"])
        except Exception as e:
            print(f"[warn] 歷史 THI 載入失敗、退回 climate normal: {e}")

    out = {}
    for ym in target_months:
        if ym in history_lookup:
            out[ym] = history_lookup[ym]
        else:
            m = int(ym.split("-")[1])
            out[ym] = monthly.get(m, 70.0)
    return pd.Series(out)


def smooth_seasonal_with_thi(seasonal_data: dict, county: str,
                               alpha: float = 0.3) -> dict:
    """用 THI 先驗平滑資料學到的季節乘子。

    Args:
      seasonal_data: 從資料學的季節乘子 {month: ratio}
      county: 該場縣市（用來查 THI 基準）
      alpha: 向 THI 先驗收斂的強度 (0=完全用資料、1=完全用 THI)

    Returns:
      平滑後的 {month: ratio}（標準化使月均=1）
    """
    if not county or not seasonal_data:
        return seasonal_data

    thi_prior = thi_predicted_seasonal(county)

    # 標準化資料學的乘子（防止整體尺度偏差）
    data_avg = sum(seasonal_data.values()) / max(1, len(seasonal_data))
    if data_avg == 0:
        data_avg = 1.0

    smoothed = {}
    for m in range(1, 13):
        d = seasonal_data.get(m, 1.0) / data_avg
        t = thi_prior.get(m, 1.0)
        smoothed[m] = (1 - alpha) * d + alpha * t

    # 重新標準化
    avg = sum(smoothed.values()) / 12
    return {m: v / avg if avg > 0 else 1.0 for m, v in smoothed.items()}
