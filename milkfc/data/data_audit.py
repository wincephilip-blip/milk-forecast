"""資料完整性檢查。

根據使用者選擇的時間範圍（預測時程 + backtest 年份），
檢查預測與驗證需要的所有資料是否齊全：
- DHI 月度資料（時序模型）
- 官方年度產量（驗證答案）
- 官方年度在養量（Method 2/3 SF）
- 官方季度在養量（Level 4 SF）
- DHI 牛/場數快取（驗證表用）
"""
import logging
from pathlib import Path
import pandas as pd
from .. import config

log = logging.getLogger("milkfc.audit")


def audit_data_for_forecast(reference_date: str = None,
                              horizon_months: int = 12,
                              backtest_years: list = None) -> dict:
    """檢查預測 + 驗證所需資料完整性。

    Args:
        reference_date: 預測基準日（YYYY-MM-DD）；None 用最新 DHI
        horizon_months: 預測時程
        backtest_years: 要做 backtest 的年份；None 表示不做 backtest

    Returns:
        {
            "ready": bool,           # 是否可以跑完整流程
            "warnings": [str],       # 非致命警告
            "errors": [str],         # 致命錯誤
            "dhi": {...},
            "annual_production": {...},
            "annual_inventory": {...},
            "quarterly_inventory": {...},
            "for_prediction": {...},
            "for_backtest": {...},
        }
    """
    out = {
        "ready": True,
        "warnings": [],
        "errors": [],
    }

    raw_dir = config.ROOT / "raw_data"

    # ---------- 1. DHI 月度資料 ----------
    dhi_files = sorted(raw_dir.glob("*dhi.xlsx"))
    dhi_years = []
    for fp in dhi_files:
        try:
            y = int(fp.stem[:4])
            dhi_years.append(y)
        except ValueError:
            continue
    dhi_years = sorted(set(dhi_years))

    out["dhi"] = {
        "n_files": len(dhi_files),
        "years": dhi_years,
        "min_year": min(dhi_years) if dhi_years else None,
        "max_year": max(dhi_years) if dhi_years else None,
    }

    # 檢查 DHI 快取（不實際載 pickle 避免 pandas 版本問題）
    cache = config.SNAPSHOT_DIR / "_cache.pkl"
    if cache.exists():
        size_mb = cache.stat().st_size / 1024 / 1024
        out["dhi"]["cache_exists"] = True
        out["dhi"]["cache_size_mb"] = round(size_mb, 1)
        # 用最新 DHI xlsx 年份推估最新月份（年底）
        if dhi_years:
            out["dhi"]["latest_sample_date"] = f"{max(dhi_years)}-12 (估)"
    else:
        out["warnings"].append("DHI 快取不存在、首次跑會比較慢（重建 ~3-5 分鐘）")

    if not dhi_years:
        out["errors"].append("沒有任何 DHI xlsx 檔案")
        out["ready"] = False

    # ---------- 2. 官方年度產量 ----------
    prod_file = raw_dir / "08--畜牧生產及貿易_牛乳產量.ods"
    out["annual_production"] = {
        "file": prod_file.name,
        "exists": prod_file.exists(),
    }
    if prod_file.exists():
        try:
            from .official_stats import OFFICIAL_ANNUAL_TONS
            yrs = sorted(OFFICIAL_ANNUAL_TONS.keys())
            out["annual_production"]["years"] = [yrs[0], yrs[-1]]
            out["annual_production"]["n_years"] = len(yrs)
            out["annual_production"]["latest_value"] = (
                OFFICIAL_ANNUAL_TONS[yrs[-1]])
        except Exception as e:
            out["warnings"].append(f"官方產量 parser 錯：{e}")
    else:
        out["warnings"].append(
            "缺官方年度產量檔（08--畜牧生產及貿易_牛乳產量.ods）"
            "→ 無法做誠實精度驗證")

    # ---------- 3. 官方年度在養量 ----------
    inv_file = raw_dir / "2-2畜牧生產113.ods"
    out["annual_inventory"] = {
        "file": inv_file.name,
        "exists": inv_file.exists(),
    }
    if inv_file.exists():
        try:
            from .official_inventory import OFFICIAL_DAIRY_INVENTORY
            yrs = sorted(OFFICIAL_DAIRY_INVENTORY.keys())
            out["annual_inventory"]["years"] = [yrs[0], yrs[-1]]
            out["annual_inventory"]["n_years"] = len(yrs)
            out["annual_inventory"]["latest"] = OFFICIAL_DAIRY_INVENTORY[yrs[-1]]
        except Exception as e:
            out["warnings"].append(f"官方在養量 parser 錯：{e}")
    else:
        out["warnings"].append(
            "缺官方年度在養量檔（2-2畜牧生產113.ods）"
            "→ 無法做 Method 2/3 SF")

    # ---------- 4. 官方季度在養量 ----------
    q_files = []
    for ext in ("xlsx", "ods"):
        for prefix in ("表1", "T1", "r表1"):
            q_files.extend(raw_dir.glob(f"{prefix}*在養整體比較*.{ext}"))
    q_files = sorted(set(q_files))
    out["quarterly_inventory"] = {
        "n_files": len(q_files),
        "files": [f.name for f in q_files[-10:]],
    }
    try:
        from .quarterly_inventory import (QUARTERLY_INVENTORY,
                                            quarter_to_decimal_year)
        loaded = sorted(QUARTERLY_INVENTORY.keys(),
                          key=quarter_to_decimal_year)
        out["quarterly_inventory"]["loaded"] = loaded
        out["quarterly_inventory"]["latest"] = loaded[-1] if loaded else None
        out["quarterly_inventory"]["n_loaded"] = len(loaded)

        # 找出檔案有但沒進到 dict 的季
        import re
        file_qs = set()
        for f in q_files:
            m = re.search(r"(\d{3})Q(\d)", f.name)
            if m:
                ad = int(m.group(1)) + 1911
                file_qs.add(f"{ad}Q{m.group(2)}")
        not_loaded = sorted(file_qs - set(loaded),
                              key=quarter_to_decimal_year)
        if not_loaded:
            out["warnings"].append(
                f"有 {len(not_loaded)} 個季報檔案在 raw_data 但未載入 "
                f"QUARTERLY_INVENTORY: {not_loaded}（請更新 quarterly_inventory.py）")
    except Exception as e:
        out["warnings"].append(f"季報解析錯：{e}")

    # ---------- 5. DHI 牛/場數快取（自動偵測缺年份並補）----------
    panel_cache = config.SNAPSHOT_DIR / "_dhi_yearly_cows.json"
    out["dhi_panel"] = {"cached": panel_cache.exists()}
    panel_years = set()
    if panel_cache.exists():
        import json
        try:
            data = json.loads(panel_cache.read_text())
            panel_years = set(int(k) for k in data.keys())
            out["dhi_panel"]["years"] = sorted(panel_years)
        except Exception:
            pass

    # 偵測缺年份：raw 有檔案但 cache 沒這年
    raw_dhi_years = set(dhi_years)  # 從 raw_data 掃出來
    missing_in_cache = sorted(raw_dhi_years - panel_years)
    out["dhi_panel"]["missing_years"] = missing_in_cache
    if missing_in_cache:
        log.info(f"  📦 偵測到 cache 缺 {missing_in_cache} 年資料、自動補...")
        try:
            from ._cow_count_extractor import extract_dhi_yearly_cows
            updated = extract_dhi_yearly_cows(years=missing_in_cache)
            # 重新載入
            data = json.loads(panel_cache.read_text())
            panel_years = set(int(k) for k in data.keys())
            out["dhi_panel"]["years"] = sorted(panel_years)
            out["dhi_panel"]["auto_refreshed"] = missing_in_cache
            log.info(f"  ✅ 自動補完 cache、新增 {len(missing_in_cache)} 年")
        except Exception as e:
            out["warnings"].append(
                f"DHI cache 缺 {missing_in_cache} 年、自動補失敗: {e}。"
                f"請手動跑：python3 -c 'from milkfc.data._cow_count_extractor "
                f"import extract_dhi_yearly_cows; extract_dhi_yearly_cows()'")

    # ---------- 6. 預測需要的資料 ----------
    if reference_date:
        try:
            ref = pd.Timestamp(reference_date)
        except Exception:
            ref = None
    else:
        # 用最新 DHI xlsx 年份的 12 月底當基準
        ref = (pd.Timestamp(f"{max(dhi_years)}-12-31")
                if dhi_years else None)

    target_years = []
    if ref is not None:
        for h in range(1, horizon_months + 1):
            t = ref + pd.DateOffset(months=h)
            target_years.append(t.year)
        target_years = sorted(set(target_years))

    out["for_prediction"] = {
        "reference_date": str(ref.date()) if ref else None,
        "horizon_months": horizon_months,
        "target_years": target_years,
    }

    # 檢查預測各目標年缺什麼
    pred_issues = []
    if ref is not None:
        max_dhi_year = max(dhi_years) if dhi_years else 0
        if ref.year > max_dhi_year:
            pred_issues.append(
                f"基準日 {ref.year} 但 DHI 只到 {max_dhi_year} 年")
    out["for_prediction"]["issues"] = pred_issues

    # ---------- 7. Backtest 各年需要的資料 ----------
    bt_status = {}
    if backtest_years:
        try:
            from .official_inventory import OFFICIAL_DAIRY_INVENTORY
            from .official_stats import OFFICIAL_ANNUAL_TONS
            from .quarterly_inventory import (QUARTERLY_INVENTORY,
                                                quarter_to_decimal_year)
        except Exception:
            OFFICIAL_DAIRY_INVENTORY = {}
            OFFICIAL_ANNUAL_TONS = {}
            QUARTERLY_INVENTORY = {}

        for y in backtest_years:
            issues = []
            # DHI 訓練資料：需要 ≤ Y-1 的 xlsx
            n_train_yrs = len([yy for yy in dhi_years if yy < y])
            if n_train_yrs < 5:
                issues.append(f"DHI 訓練資料 < 5 年（只有 {n_train_yrs}）")

            # 答案：需要該年官方產量
            if y not in OFFICIAL_ANNUAL_TONS:
                issues.append(f"缺 {y} 年官方產量、無法算誤差")

            # 在養量：需要 Y-1 的官方場數/牛數
            if (y - 1) not in OFFICIAL_DAIRY_INVENTORY:
                issues.append(f"缺 {y-1} 年官方在養量")

            # 季報資料覆蓋
            n_q_pre = sum(1 for qid in QUARTERLY_INVENTORY
                            if quarter_to_decimal_year(qid) < y)
            if n_q_pre == 0:
                issues.append(f"沒有 {y} 年前的季報、L4 退化成線性外推")

            bt_status[y] = {
                "n_dhi_train_years": n_train_yrs,
                "has_truth": y in OFFICIAL_ANNUAL_TONS,
                "n_quarterly_pre": n_q_pre,
                "issues": issues,
                "ok": len(issues) == 0 or all("退化" in i for i in issues),
            }

    out["for_backtest"] = bt_status
    return out


def print_audit_report(audit: dict):
    """把 audit dict 印成漂亮的 console 報表。"""
    print()
    print("=" * 60)
    print("📋 資料完整性檢查")
    print("=" * 60)

    # DHI
    d = audit["dhi"]
    if d.get("years"):
        latest = d.get("latest_sample_date", "?")
        print(f"\n✅ DHI 月度資料：{d['n_files']} 檔 "
              f"({d['min_year']}–{d['max_year']})、最新 {latest}")
        if "n_rows" in d:
            print(f"   {d['n_rows']:,} 筆紀錄、{d['n_farms']} 場")
    else:
        print("\n❌ 沒有任何 DHI xlsx")

    # 官方產量
    p = audit["annual_production"]
    if p.get("exists"):
        yrs = p.get("years", [])
        print(f"\n✅ 官方年度產量：{yrs[0] if yrs else '?'}–"
              f"{yrs[1] if len(yrs)>1 else '?'}（{p.get('n_years')} 年）")
    else:
        print(f"\n❌ 缺官方產量檔（{p['file']}）")

    # 官方在養量
    inv = audit["annual_inventory"]
    if inv.get("exists"):
        yrs = inv.get("years", [])
        print(f"\n✅ 官方年度在養量：{yrs[0] if yrs else '?'}–"
              f"{yrs[1] if len(yrs)>1 else '?'}（{inv.get('n_years')} 年）")
    else:
        print(f"\n❌ 缺官方在養量檔（{inv['file']}）")

    # 季報
    q = audit["quarterly_inventory"]
    print(f"\n📊 官方季度在養量：{q.get('n_loaded', 0)} 季載入"
          f"（檔案 {q['n_files']} 個）")
    if q.get("loaded"):
        print(f"   範圍：{q['loaded'][0]} ~ {q['loaded'][-1]}")

    # DHI panel cache
    pc = audit["dhi_panel"]
    if pc.get("cached"):
        yrs = pc.get("years", [])
        print(f"\n✅ DHI 牛/場數快取：{yrs[0] if yrs else '?'}–"
              f"{yrs[-1] if yrs else '?'}（{len(yrs)} 年）", end='')
        if pc.get("auto_refreshed"):
            print(f"  📦 自動補了 {pc['auto_refreshed']} 年")
        else:
            print()
    else:
        print("\n⚠️  DHI 牛/場數快取沒生過、首次跑 backtest 會花約 4 分鐘")

    # 預測
    fp = audit["for_prediction"]
    if fp.get("target_years"):
        print(f"\n🔮 預測目標：{fp['target_years']}（基準日 {fp['reference_date']}、"
              f"{fp['horizon_months']} 個月）")
        if fp.get("issues"):
            for i in fp["issues"]:
                print(f"   ⚠️  {i}")

    # Backtest
    bt = audit["for_backtest"]
    if bt:
        print(f"\n🎯 Backtest 各年資料：")
        print(f"   {'年':>5}  {'訓練年':>6}  {'真值':>4}  {'季報':>4}  狀態")
        for y, info in sorted(bt.items()):
            ok = "✓" if info["ok"] else "⚠"
            truth = "✓" if info["has_truth"] else "✗"
            print(f"   {y:>5}  {info['n_dhi_train_years']:>6}  {truth:>4}  "
                  f"{info['n_quarterly_pre']:>4}  {ok}")
            for i in info["issues"]:
                print(f"          ⚠️  {i}")

    # Errors / Warnings
    if audit["errors"]:
        print(f"\n❌ 致命錯誤：")
        for e in audit["errors"]:
            print(f"   {e}")
    if audit["warnings"]:
        print(f"\n⚠️  警告：")
        for w in audit["warnings"]:
            print(f"   {w}")

    print()
    if audit["ready"]:
        print("✅ 資料齊全、可以跑")
    else:
        print("❌ 資料不齊、修復後再跑")
    print("=" * 60)
