"""農業部畜禽飼養場數及在養量季報。

來源：表1 / 表2 在養量比較.xlsx
比年報新很多（年報延遲 6-12 個月、季報延遲 3 個月）。

只用「**在養量**」資料、不碰「產量」（產量是答案、不能進預測）。
"""
from pathlib import Path
import pandas as pd
from .. import config


# 21 季全國（臺閩地區）資料：2019 Q1 ~ 2025 Q3
QUARTERLY_INVENTORY = {
    "2019Q1": {"n_dairy_farms": 557, "n_dairy_cattle": 115350, "n_milking_farms": 530, "n_milking_cows": 62417},
    "2019Q2": {"n_dairy_farms": 557, "n_dairy_cattle": 115685, "n_milking_farms": 534, "n_milking_cows": 62580},
    "2019Q3": {"n_dairy_farms": 559, "n_dairy_cattle": 115694, "n_milking_farms": 533, "n_milking_cows": 62005},
    "2020Q1": {"n_dairy_farms": 557, "n_dairy_cattle": 116944, "n_milking_farms": 534, "n_milking_cows": 62829},
    "2020Q2": {"n_dairy_farms": 555, "n_dairy_cattle": 117344, "n_milking_farms": 532, "n_milking_cows": 62531},
    "2020Q3": {"n_dairy_farms": 562, "n_dairy_cattle": 117840, "n_milking_farms": 533, "n_milking_cows": 63115},
    "2021Q1": {"n_dairy_farms": 558, "n_dairy_cattle": 120212, "n_milking_farms": 530, "n_milking_cows": 63754},
    "2021Q2": {"n_dairy_farms": 556, "n_dairy_cattle": 124779, "n_milking_farms": 532, "n_milking_cows": 66034},
    "2021Q3": {"n_dairy_farms": 559, "n_dairy_cattle": 125726, "n_milking_farms": 533, "n_milking_cows": 65632},
    "2022Q1": {"n_dairy_farms": 565, "n_dairy_cattle": 126062, "n_milking_farms": 529, "n_milking_cows": 65964},
    "2022Q2": {"n_dairy_farms": 566, "n_dairy_cattle": 126035, "n_milking_farms": 529, "n_milking_cows": 66326},
    "2022Q3": {"n_dairy_farms": 563, "n_dairy_cattle": 126264, "n_milking_farms": 526, "n_milking_cows": 65028},
    "2023Q1": {"n_dairy_farms": 560, "n_dairy_cattle": 126413, "n_milking_farms": 518, "n_milking_cows": 64625},
    "2023Q2": {"n_dairy_farms": 557, "n_dairy_cattle": 127068, "n_milking_farms": 515, "n_milking_cows": 65045},
    "2023Q3": {"n_dairy_farms": 554, "n_dairy_cattle": 125933, "n_milking_farms": 513, "n_milking_cows": 64283},
    "2024Q1": {"n_dairy_farms": 554, "n_dairy_cattle": 122441, "n_milking_farms": 509, "n_milking_cows": 61411},
    "2024Q2": {"n_dairy_farms": 554, "n_dairy_cattle": 120706, "n_milking_farms": 508, "n_milking_cows": 61447},
    "2024Q3": {"n_dairy_farms": 548, "n_dairy_cattle": 120474, "n_milking_farms": 501, "n_milking_cows": 60950},
    "2025Q1": {"n_dairy_farms": 547, "n_dairy_cattle": 119212, "n_milking_farms": 499, "n_milking_cows": 60142},
    "2025Q2": {"n_dairy_farms": 546, "n_dairy_cattle": 118923, "n_milking_farms": 500, "n_milking_cows": 60018},
    "2025Q3": {"n_dairy_farms": 542, "n_dairy_cattle": 118377, "n_milking_farms": 497, "n_milking_cows": 59779},
}


def quarter_to_decimal_year(qid: str) -> float:
    """'2025Q3' → 2025.625（Q3 中點：8/15 ≈ 0.625）"""
    y = int(qid[:4])
    q = int(qid[5])
    midpoint = {1: 0.125, 2: 0.375, 3: 0.625, 4: 0.875}[q]
    return y + midpoint


def get_latest_quarter() -> tuple:
    """回傳 (quarter_id, data_dict) 最新一季。"""
    qid = max(QUARTERLY_INVENTORY.keys(),
                key=lambda x: quarter_to_decimal_year(x))
    return qid, QUARTERLY_INVENTORY[qid]


def load_quarterly_inventory(table1_path: Path = None) -> dict:
    """從 ods/xlsx 重新解析（資料更新時用）。

    解析臺閩地區 sheet 的「乳牛」+「產乳牛」兩列、3 個季度（當季、上季、去年同季）。
    """
    if table1_path is None:
        table1_path = config.ROOT / "raw_data" / "表1  114Q3在養整體比較.xlsx"
    if not table1_path.exists():
        return dict(QUARTERLY_INVENTORY)

    df = pd.read_excel(table1_path, sheet_name="臺閩地區", header=None)
    # 從 row 1 找 quarter labels（如「114年第3季 2025Q3」）
    quarter_label_row = df.iloc[3]  # ['畜禽別','Livestock','114年第3季\n2025Q3', ...]
    # 找出 Q label 字串
    quarter_ids = []
    for v in quarter_label_row.tolist():
        if isinstance(v, str) and "Q" in v:
            # 取出 'YYYYQx' 部分
            for tok in v.split("\n"):
                tok = tok.strip()
                if len(tok) == 6 and tok[:4].isdigit() and tok[4] == "Q":
                    quarter_ids.append(tok)
                    break
    # row 11 = 乳牛、row 12 = 產乳牛
    out = dict(QUARTERLY_INVENTORY)
    dairy_row = df.iloc[11]  # ['乳牛','Dairy Cattle', farms_curr, heads_curr, ...]
    milking_row = df.iloc[12]  # ['產乳牛','Milking Cow', ...]

    # 欄位順序：col 2,3 = 當季 farms,heads；col 4,5 = 上季；col 6,7 = 去年同季
    if len(quarter_ids) >= 3:
        for idx, qid in enumerate(quarter_ids[:3]):
            f_col = 2 + idx * 2
            h_col = 3 + idx * 2
            try:
                out[qid] = {
                    "n_dairy_farms": int(dairy_row.iloc[f_col]),
                    "n_dairy_cattle": int(dairy_row.iloc[h_col]),
                    "n_milking_farms": int(milking_row.iloc[f_col]),
                    "n_milking_cows": int(milking_row.iloc[h_col]),
                }
            except (ValueError, TypeError):
                continue
    return out


def estimate_official_for_year(target_year: int,
                                 quarterly: dict = None,
                                 annual: dict = None) -> dict:
    """估給定年份的全國官方在養量（場數、產乳牛、乳牛）。

    優先用：最新季報 → 年報線性外推 → 退回最後一年年報

    Args:
        target_year: 預估年（西元）
        quarterly: {qid: {...}}（默認 QUARTERLY_INVENTORY）
        annual: {year: {n_farms, n_milking_cows, ...}}（默認 OFFICIAL_DAIRY_INVENTORY）

    Returns:
        {n_milking_farms, n_milking_cows, source, source_year}
    """
    from .official_inventory import OFFICIAL_DAIRY_INVENTORY
    quarterly = quarterly or QUARTERLY_INVENTORY
    annual = annual or OFFICIAL_DAIRY_INVENTORY

    # 把所有資料點（年/季）轉成 (decimal_year, n_milking_cows, n_dairy_farms)
    # 注意：年報「養乳牛場數」(info["n_farms"]) ≈ 季報「乳牛場數」(n_dairy_farms)
    # 不是「產乳牛場數」(n_milking_farms) — 後者是當下實際泌乳的場
    points = []
    for y, info in annual.items():
        points.append((float(y) + 0.5,
                        info["n_milking_cows"],
                        info["n_farms"]))
    for qid, info in quarterly.items():
        dy = quarter_to_decimal_year(qid)
        points.append((dy,
                        info["n_milking_cows"],
                        info["n_dairy_farms"]))
    points.sort()

    # 用最近 3-5 個資料點線性回歸
    import numpy as np
    if len(points) < 2:
        latest = points[-1]
        return {
            "n_milking_cows": latest[1],
            "n_dairy_farms": latest[2],
            "n_milking_farms": latest[2],
            "source": "last_observed",
            "source_year": latest[0],
            "n_data_points": len(points),
        }

    # 取最近 5 點做回歸
    recent = points[-5:]
    xs = np.array([p[0] for p in recent])
    ys_cows = np.array([p[1] for p in recent])
    ys_farms = np.array([p[2] for p in recent])

    # 線性回歸
    slope_c, b_c = np.polyfit(xs, ys_cows, 1)
    slope_f, b_f = np.polyfit(xs, ys_farms, 1)

    target_x = target_year + 0.5  # 取年中
    pred_cows = float(slope_c * target_x + b_c)
    pred_farms = float(slope_f * target_x + b_f)

    return {
        "n_milking_cows": pred_cows,
        "n_dairy_farms": pred_farms,
        "n_milking_farms": pred_farms,  # alias for backward compat
        "source": "extrapolation",
        "source_data": [{"x": float(p[0]), "cows": float(p[1]),
                          "farms": float(p[2])} for p in recent],
        "slope_cows_per_year": float(slope_c),
        "slope_farms_per_year": float(slope_f),
        "n_data_points": len(recent),
    }
