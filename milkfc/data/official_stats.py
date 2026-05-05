"""農業部統計處官方年度乳量資料。

來源：08--畜牧生產及貿易_牛乳產量.ods
單位：公噸（metric tons）
民國年 + 1911 = 西元年
"""
from pathlib import Path
import pandas as pd
from .. import config


# 預先擷取（避免 runtime 依賴 ods 解析）：
# 民國 56年 (1967) ~ 113年 (2024)
OFFICIAL_ANNUAL_TONS = {
    1967: 13812,    1968: 14798,    1969: 14966,    1970: 16123,
    1971: 17906,    1972: 22932,    1973: 37640,    1974: 41879,
    1975: 46189,    1976: 45111,    1977: 45727,    1978: 44615,
    1979: 44418,    1980: 47740,    1981: 50154,    1982: 55859,
    1983: 58022,    1984: 66933,    1985: 87879,    1986: 109723,
    1987: 144390,   1988: 173407,   1989: 182421,   1990: 203830,
    1991: 225656,   1992: 246281,   1993: 278476,   1994: 289574,
    1995: 317806,   1996: 315876,   1997: 330469,   1998: 338369,
    1999: 338004.776,   2000: 358049,   2001: 345969.706,
    2002: 357804.117,   2003: 354420.629,   2004: 322660.322,
    2005: 303496.494,   2006: 323164.797,   2007: 322220.117,
    2008: 315559.317,   2009: 322099.657,   2010: 336036.415,
    2011: 350894.071,   2012: 348489.097,   2013: 358145.6,
    2014: 363145.36,    2015: 375498.835,   2016: 378488.421,
    2017: 386361.874,   2018: 419341.805,   2019: 431879.283,
    2020: 437154.578,   2021: 449214.217,   2022: 463094.868,
    2023: 472449.164,   2024: 452413.589,
    # 2025 (114年) 尚未公布
}


def load_official_annual(filepath: Path = None) -> dict:
    """從 ods 重新解析（在原始檔更新時用）。回傳 {year: tons}。"""
    if filepath is None:
        filepath = config.ROOT / "raw_data" / "08--畜牧生產及貿易_牛乳產量.ods"
    if not filepath.exists():
        return dict(OFFICIAL_ANNUAL_TONS)
    df = pd.read_excel(filepath, sheet_name="牛乳產量",
                        engine="odf", header=None)
    out = {}
    for _, row in df.iterrows():
        roc = row[1]
        val = row[3]
        if not isinstance(roc, str) or not roc.endswith("年"):
            continue
        try:
            roc_year = int(roc.replace("年", "").strip())
        except ValueError:
            continue
        ad_year = roc_year + 1911
        try:
            tons = float(val)
        except (ValueError, TypeError):
            continue
        out[ad_year] = tons
    return out


def get_official_tons(year: int) -> float:
    """取得指定年的官方產量（公噸）。回傳 None 若無資料。"""
    return OFFICIAL_ANNUAL_TONS.get(int(year))


def historical_ratios(dhi_yearly_tons: dict) -> dict:
    """各年的 (官方/DHI) 比值，用於 scale factor 校正。

    Args:
        dhi_yearly_tons: {year: tons} 自 DHI 加總
    Returns:
        {year: {dhi_tons, official_tons, ratio}}
    """
    out = {}
    for y, dhi in dhi_yearly_tons.items():
        off = OFFICIAL_ANNUAL_TONS.get(int(y))
        if off is None or dhi <= 0:
            continue
        out[int(y)] = {
            "dhi_tons": float(dhi),
            "official_tons": float(off),
            "ratio": float(off) / float(dhi),
        }
    return out
