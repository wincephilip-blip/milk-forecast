"""誠實的預測 vs 答案比較。

設計原則：
- 「答案」= 農業部官方產乳量（08--畜牧生產及貿易_牛乳產量.ods）
- 「預測」絕對不能用「答案」當輸入，否則就是循環論證
- 預測只能用：DHI 原始資料 + 在養量（產乳牛、場數）等獨立資料

提供三種預測方法、都只用 DHI + 在養量、不用產量：

Method 1 「固定比值」（baseline）：
   預測 = DHI 加總 × 固定 scale factor (從牛數比例推算的常數)
   缺點：DHI 涵蓋率年年變、固定值在每一年都不對

Method 2 「場數比例」：
   預測 = DHI 加總 × (官方場數 / DHI 場數)
   優點：只用「場數」這個在養量資料

Method 3 「結構分解（含 productivity 校正）」：
   預測 = 官方產乳牛 × DHI 平均單頭日產乳 × 305 天 ÷ productivity 比率
   說明：DHI 樣本場單頭產量比全國平均高 6-12%、productivity 比率（DHI/全國）
        從歷年資料線性外推到目標年（避免循環引用）
   優點：物理意義最強（與 cohort_simple 預測模型同步邏輯）

把三種方法逐年對照官方真值、算誤差，給人看「哪一種預測最準」。
"""
import json
import logging
from pathlib import Path
from collections import defaultdict
import numpy as np

from ..data.official_inventory import OFFICIAL_DAIRY_INVENTORY
from .. import config

log = logging.getLogger("milkfc.validation")


def compare_with_official(dhi_yearly_panel: dict,
                            dhi_annual_milk_tons: dict,
                            method_1_scale_factor: float = 2.64,
                            min_year: int = 2018,
                            max_year: int = 2024) -> dict:
    """逐年比較三種預測方法 vs 官方真值。

    Args:
        dhi_yearly_panel: {year: {n_cows, n_farms, n_records, dhi_total_kg}}
            來自 _cow_count_extractor，每年 DHI 涵蓋的場/牛/紀錄數 + 乳量加總
        dhi_annual_milk_tons: {year: tons} DHI 月度時序加總（每月公噸）
        method_1_scale_factor: 固定 scale factor（基準法用）
        min_year, max_year: 比較範圍

    Returns:
        {
            "rows": [...],
            "summary": {method_1, method_2, method_3, best_method},
            ...
        }
    """
    # 預先算 DHI/全國 productivity 比率歷史（給 M3 用）
    # 全國 = 公告產量 / 公告牛口 / 305、DHI = test_kg / n_records
    productivity_ratios = {}
    for y, info in OFFICIAL_DAIRY_INVENTORY.items():
        nat_yield = (info["production_tons"] * 1000 / info["n_milking_cows"] / 305
                      if info.get("n_milking_cows") and info.get("production_tons")
                      else None)
        panel = dhi_yearly_panel.get(y, {})
        dhi_yield = (panel.get("dhi_total_kg") / panel.get("n_records")
                      if panel.get("n_records") else None)
        if nat_yield and dhi_yield and nat_yield > 0:
            productivity_ratios[y] = dhi_yield / nat_yield

    def _extrapolate_ratio(target_year, n_recent=5):
        """從 < target_year 的歷史外推 productivity 比率（避免循環）。"""
        past = {yy: r for yy, r in productivity_ratios.items() if yy < target_year}
        if not past:
            return 1.0
        sorted_items = sorted(past.items())
        recent = sorted_items[-n_recent:]
        if len(recent) < 2:
            return list(past.values())[-1]
        xs = np.array([yy + 0.5 for yy, _ in recent])
        ys = np.array([r for _, r in recent])
        slope, b = np.polyfit(xs, ys, 1)
        pred = float(slope * (target_year + 0.5) + b)
        return max(1.0, pred)  # 下限 1.0、防止低估

    years = sorted([y for y in OFFICIAL_DAIRY_INVENTORY
                     if min_year <= y <= max_year])
    rows = []

    for y in years:
        official = OFFICIAL_DAIRY_INVENTORY[y]
        off_cows = official["n_milking_cows"]
        off_farms = official["n_farms"]
        off_prod = official["production_tons"]  # ← 答案，不能拿來做 input

        panel = dhi_yearly_panel.get(y, {})
        dhi_cows = panel.get("n_cows")
        dhi_farms = panel.get("n_farms")
        dhi_records = panel.get("n_records")
        dhi_test_kg = panel.get("dhi_total_kg")  # 單日乳量加總
        dhi_yearly_tons = dhi_annual_milk_tons.get(y)

        # Method 1: 固定 SF
        m1 = dhi_yearly_tons * method_1_scale_factor if dhi_yearly_tons else None
        m1_err = ((m1 - off_prod) / off_prod * 100) if m1 else None

        # Method 2: 場數比例
        m2 = (dhi_yearly_tons * (off_farms / dhi_farms)
                if dhi_yearly_tons and dhi_farms else None)
        m2_err = ((m2 - off_prod) / off_prod * 100) if m2 else None

        # Method 3: 結構分解（含 productivity 校正、與 cohort_simple 同步）
        # 從 < y 的歷史外推 productivity 比率（避免用「答案」）
        m3 = None
        m3_err = None
        ratio_y = None
        if dhi_test_kg and dhi_records and off_cows:
            avg_daily = dhi_test_kg / dhi_records  # kg/cow/day (DHI 樣本)
            ratio_y = _extrapolate_ratio(y)
            # 校正後等於用「全國平均單頭產量」
            corrected_daily = avg_daily / ratio_y
            per_cow_year = corrected_daily * 305  # kg/cow/year
            m3 = (per_cow_year * off_cows) / 1000  # kg → 公噸
            m3_err = (m3 - off_prod) / off_prod * 100

        rows.append({
            "year": y,
            "official_production": off_prod,
            "official_milking_cows": off_cows,
            "official_n_farms": off_farms,
            "dhi_n_cows": dhi_cows,
            "dhi_n_farms": dhi_farms,
            "dhi_n_records": dhi_records,
            "dhi_yearly_tons": dhi_yearly_tons,
            "dhi_avg_daily_kg": (dhi_test_kg / dhi_records
                                  if dhi_test_kg and dhi_records else None),
            "method_1_pred": m1,
            "method_1_err_pct": m1_err,
            "method_2_pred": m2,
            "method_2_err_pct": m2_err,
            "method_3_pred": m3,
            "method_3_err_pct": m3_err,
            "method_3_productivity_ratio": ratio_y,
            "farm_ratio": (off_farms / dhi_farms) if dhi_farms else None,
            "cow_ratio": (off_cows / dhi_cows) if dhi_cows else None,
        })

    def _summarize(key):
        errs = [abs(r[key]) for r in rows if r.get(key) is not None]
        if not errs:
            return {}
        return {
            "mape": float(np.mean(errs)),
            "max_err_pct": float(max(errs)),
            "min_err_pct": float(min(errs)),
            "bias": float(np.mean([r[key] for r in rows
                                    if r.get(key) is not None])),
        }

    s1 = _summarize("method_1_err_pct")
    s2 = _summarize("method_2_err_pct")
    s3 = _summarize("method_3_err_pct")
    methods = [("method_1", s1, "固定 SF"),
                ("method_2", s2, "場數比例"),
                ("method_3", s3, "結構分解")]
    best = min(methods, key=lambda x: x[1].get("mape", 1e9) if x[1] else 1e9)

    summary = {
        "n_years": len(rows),
        "method_1": {**s1, "name": "固定 SF",
                     "formula": f"DHI 加總 × {method_1_scale_factor}"},
        "method_2": {**s2, "name": "場數比例",
                     "formula": "DHI 加總 × (農業部公告場數 / DHI 場數)"},
        "method_3": {**s3, "name": "結構分解（含 productivity 校正）",
                     "formula": "農業部公告產乳牛 × (DHI 平均單頭日產乳 ÷ productivity 比率) × 305"},
        "best_method": best[0],
        "method_1_scale_factor": method_1_scale_factor,
    }

    return {
        "rows": rows,
        "summary": summary,
    }


def run_comparison(snapshot_id: str = None,
                    out_path: Path = None) -> dict:
    """從最近的 ts_ snapshot + DHI 牛/場快取，跑完整比較。"""
    from ..data._cow_count_extractor import extract_dhi_yearly_cows

    # 讀 DHI 場/牛/記錄數（已快取；只取有官方真值的年份）
    dhi_panel = extract_dhi_yearly_cows(years=list(range(2018, 2025)))

    # 讀時序 snapshot 拿 DHI 月度加總 → 換成年度公噸
    if snapshot_id is None:
        snaps = sorted(config.SNAPSHOT_DIR.glob("ts_*"), reverse=True)
        if not snaps:
            raise FileNotFoundError("找不到 ts_ snapshot")
        snap_dir = snaps[0]
    else:
        snap_dir = config.SNAPSHOT_DIR / snapshot_id

    with open(snap_dir / "ts_results.json") as f:
        results = json.load(f)
    nat = results.get("全國") or results.get("National")
    if not nat:
        raise ValueError("snapshot 沒有「全國」序列")

    yearly_kg = defaultdict(float)
    for p in nat["series_history"]:
        y = int(str(p["yyyymm"])[:4])
        yearly_kg[y] += float(p["value"])
    dhi_annual_tons = {y: v / 1000.0 for y, v in yearly_kg.items()}

    out = compare_with_official(dhi_panel, dhi_annual_tons)
    out["snapshot_id"] = snap_dir.name

    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, ensure_ascii=False, default=str)

    s = out["summary"]
    log.info(f"  Method 1 (固定 SF) MAPE = {s['method_1'].get('mape',0):.1f}%")
    log.info(f"  Method 2 (場數比例) MAPE = {s['method_2'].get('mape',0):.1f}%")
    log.info(f"  Method 3 (結構分解) MAPE = {s['method_3'].get('mape',0):.1f}%")
    log.info(f"  最佳方法: {s['best_method']}")
    return out
