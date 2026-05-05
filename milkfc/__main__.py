"""CLI: python -m milkfc <command>

Commands:
  run          跑一次完整 pipeline，產出快照與儀表板
  validate     只跑資料驗證
  status       顯示最近一次快照狀態
  diagnose     對最近快照產出異常診斷報告
  list-snaps   列出所有快照
"""
import sys
import json
import argparse
import logging
import pickle
from pathlib import Path
from datetime import datetime

from . import config, __version__, __model_version__

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%H:%M:%S',
)

def cmd_run(args):
    from .pipeline import run_pipeline
    res = run_pipeline(
        farm_ids=args.farms.split(",") if args.farms else None,
        train_end=args.train_end,
        target_year=args.target_year,
        backtest=not args.no_backtest,
        n_sim=args.n_sim,
        mode=args.mode,
        train_window_months=args.train_window,
        auto_window=False if args.no_auto_window else None,
        horizon_months=args.horizon_months,
        reference_date=args.reference_date,
    )
    print(f"\n[OK] snapshot {res['snapshot_id']}")
    print(f"     dir: {res['snapshot_dir']}")
    print(f"     processed: {res['snapshot']['n_farms_processed']} farms")

    if args.dashboard:
        from .dashboard.builder import build_dashboard
        out = build_dashboard(res["snapshot_id"])
        print(f"     dashboard: {out}")

def cmd_reload(args):
    """強制重新載入 DHI（用於上傳新年度檔後）"""
    from .data import load_combined
    cache = config.SNAPSHOT_DIR / "_cache.pkl"
    if cache.exists():
        cache.unlink()
        print(f"[i] 已刪除舊快取 {cache}")
    df = load_combined(cache, force_reload=True)
    print(f"[OK] 重新載入完成: {len(df):,} 筆、{df['farm_id'].nunique()} 場")
    print(f"     資料時間範圍: {df['sample_date'].min().date()} → {df['sample_date'].max().date()}")
    print(f"     年份分布: {sorted(df['year'].dropna().unique().astype(int).tolist())}")


def cmd_validate(args):
    from .data import load_combined, validate_dhi
    df = load_combined(config.SNAPSHOT_DIR / "_cache.pkl")
    val = validate_dhi(df)
    print(json.dumps(val, indent=2, ensure_ascii=False, default=str))

def cmd_list_snaps(args):
    snaps = sorted(config.SNAPSHOT_DIR.glob("2*"), reverse=True)
    if not snaps:
        print("No snapshots yet.")
        return
    print(f"{'Snapshot ID':<20} {'Farms':>7} {'Failed':>7}")
    for s in snaps[:20]:
        try:
            with open(s / "manifest.json") as f:
                m = json.load(f)
            print(f"{s.name:<20} {m.get('n_farms_processed','?'):>7} "
                  f"{m.get('n_farms_failed','?'):>7}")
        except Exception:
            print(f"{s.name:<20} (error reading manifest)")

def cmd_status(args):
    snaps = sorted(config.SNAPSHOT_DIR.glob("2*"), reverse=True)
    if not snaps:
        print("No snapshots yet.")
        return
    latest = snaps[0]
    with open(latest / "manifest.json") as f:
        m = json.load(f)
    print(f"=== Latest snapshot: {m['snapshot_id']} ===")
    print(f"  Time:        {m['timestamp']}")
    print(f"  Pkg ver:     {m['package_version']}")
    print(f"  Model ver:   {m['model_version']}")
    print(f"  Data hash:   {m['data_hash']}")
    print(f"  Farms OK:    {m['n_farms_processed']}")
    print(f"  Farms fail:  {m['n_farms_failed']}")
    print(f"  Elapsed:     {m['elapsed_seconds']:.0f}s")
    if m["validation"]["warnings"]:
        print(f"  Warnings:")
        for w in m["validation"]["warnings"]:
            print(f"    - {w}")

def cmd_analyze(args):
    """產生描述性分析儀表板（月度分布、泌乳曲線）"""
    from .data import load_combined
    df = load_combined(config.SNAPSHOT_DIR / "_cache.pkl")

    year_range = None
    if args.year_range:
        y0, y1 = map(int, args.year_range.split("-"))
        year_range = (y0, y1)
        print(f"[i] 篩選年度: {y0} - {y1}")

    if args.view in ("seasonal", "all"):
        print(f"[i] 產生月度分布儀表板...")
        from .dashboard.seasonal_dashboard import build_seasonal_dashboard
        out = build_seasonal_dashboard(df, year_range=year_range)
        print(f"    → {out}")

    if args.view in ("lactation", "all"):
        print(f"[i] 產生泌乳曲線儀表板...")
        from .dashboard.lactation_dashboard import build_lactation_dashboard
        out = build_lactation_dashboard(df, year_range=year_range)
        print(f"    → {out}")

    print("[OK] analyze 完成")


def _interactive_setup(args):
    """互動式詢問預測時程與基準日。Cron / CI 環境會自動跳過。"""
    import sys
    # 偵測是否為互動 terminal（cron 不是）
    if not sys.stdin.isatty() or getattr(args, 'non_interactive', False):
        return
    # 若任何相關旗標已給就不再詢問
    explicit = (getattr(args, 'horizon_months', None) is not None or
                getattr(args, 'reference_date', None) is not None or
                getattr(args, 'train_window', None) is not None or
                getattr(args, 'no_auto_window', False))
    if explicit:
        return

    # 載入快取看「資料現在到哪」當預設基準
    try:
        from .data import load_combined
        df = load_combined(config.SNAPSHOT_DIR / "_cache.pkl")
        latest = df["sample_date"].max()
        latest_str = latest.date().isoformat()
        if latest.month == 12:
            default_val_year = latest.year
        else:
            default_val_year = latest.year - 1
    except Exception:
        latest_str = "(無法判讀)"
        default_val_year = "(自動)"

    print()
    print("="*60)
    print("📅 預測範圍設定")
    print("="*60)
    print(f"資料目前最新日期: {latest_str}")
    print(f"預設驗證年份: {default_val_year}")
    print(f"預設基準日: 自動 (= 各場最新資料日)")
    print(f"預設預測時程: 12 個月")
    print()

    # 詢問預測時程
    print("【預測時程選擇】")
    print("  1) 12 個月（標準，預設）")
    print("  2) 6 個月（短期供需）")
    print("  3) 18 個月")
    print("  4) 24 個月（跨年規劃）")
    print("  5) 自訂月數")
    choice = input("請選 [1]: ").strip() or "1"
    horizon_map = {"1": 12, "2": 6, "3": 18, "4": 24}
    if choice == "5":
        try:
            args.horizon_months = int(input("輸入月數: ").strip())
        except ValueError:
            args.horizon_months = 12
    else:
        args.horizon_months = horizon_map.get(choice, 12)

    # 詢問基準日
    print()
    print("【基準日選擇】（決定『現在是哪一天』、預測從哪天往後算）")
    print(f"  1) 自動（用每場最新資料日，最常用，預設）")
    print(f"  2) 強制設為某日（例如要做 what-if 回顧分析）")
    choice = input("請選 [1]: ").strip() or "1"
    if choice == "2":
        d = input("輸入基準日 (YYYY-MM-DD): ").strip()
        if d:
            args.reference_date = d

    # 詢問訓練視窗
    print()
    print("【訓練視窗選擇】（用多少歷史資料來訓練模型）")
    print("  1) 自動 - 每場跑內部回測選最佳視窗（最準，預設）")
    print("  2) 全部用 24 個月（適合管理快變化的場）")
    print("  3) 全部用 36 個月")
    print("  4) 全部用 48 個月（驗證最佳的單一視窗）")
    print("  5) 全部用 60 個月（資料越多越穩）")
    print("  6) 用全部歷史資料")
    print("  7) 自訂月數")
    choice = input("請選 [1]: ").strip() or "1"
    window_map = {"2": 24, "3": 36, "4": 48, "5": 60, "6": 999}
    if choice == "1":
        # 維持自動模式
        args.train_window = None
        args.no_auto_window = False
    elif choice == "7":
        try:
            args.train_window = int(input("輸入月數: ").strip())
            args.no_auto_window = True
        except ValueError:
            args.train_window = None
            args.no_auto_window = False
    elif choice in window_map:
        args.train_window = window_map[choice]
        args.no_auto_window = True
    else:
        args.train_window = None
        args.no_auto_window = False

    # 預覽
    print()
    print("="*60)
    print("即將執行：")
    print(f"  預測時程: {args.horizon_months} 個月")
    if args.reference_date:
        print(f"  基準日:   {args.reference_date}（強制）")
        try:
            ref = pd.Timestamp(args.reference_date)
            end = ref + pd.DateOffset(months=args.horizon_months)
            print(f"  → 將預測 {ref.date()} → {end.date()}")
        except Exception:
            pass
    else:
        print(f"  基準日:   自動偵測（各場最新資料日）")
        print(f"  → 將自動推導歷史驗證年 + 未來 {args.horizon_months} 月")
    if args.train_window:
        print(f"  訓練視窗: 固定 {args.train_window} 個月")
    else:
        print(f"  訓練視窗: 自動每場選最佳（24/36/48/60 候選）")
    print("="*60)
    confirm = input("確認執行？[Y/n]: ").strip().lower()
    if confirm == "n":
        print("已取消")
        sys.exit(0)


def cmd_monthly(args):
    """月度一鍵全跑：驗證 + 預測 + 月度分布 + 泌乳曲線。

    這個指令適合放進 cron 每月排程，會依序：
      1. 跑 combined 模式預測（產出 dashboard.html）
      2. 產生月度分布儀表板（seasonal.html）
      3. 產生泌乳曲線儀表板（lactation.html）
      4. 印出異常告警摘要
    """
    import time, pandas as pd

    # 互動式設定（cron 自動跳過）
    _interactive_setup(args)

    t_start = time.time()
    print("=" * 60)
    print(f"milkfc monthly run @ {datetime.now().isoformat()}")
    print("=" * 60)

    # === Step 1: 預測 (combined mode + dashboard) ===
    print("\n[1/4] 跑預測（combined 模式）...")
    from .pipeline import run_pipeline
    res = run_pipeline(
        farm_ids=args.farms.split(",") if args.farms else None,
        backtest=True,
        n_sim=args.n_sim,
        mode="combined",
        train_window_months=getattr(args, 'train_window', None),
        auto_window=False if getattr(args, 'no_auto_window', False) else None,
        horizon_months=getattr(args, 'horizon_months', None),
        reference_date=getattr(args, 'reference_date', None),
    )
    print(f"  → 處理 {res['snapshot']['n_farms_processed']} 場")
    print(f"  → 失敗 {res['snapshot']['n_farms_failed']} 場")

    print("\n[2/4] 產生預測儀表板 dashboard.html ...")
    from .dashboard.builder import build_dashboard
    out = build_dashboard(res["snapshot_id"])
    print(f"  → {out}")

    # === Step 3 & 4: 描述性分析 ===
    if not args.skip_analyze:
        from .data import load_combined
        df = load_combined(config.SNAPSHOT_DIR / "_cache.pkl")

        year_range = None
        if args.year_range:
            y0, y1 = map(int, args.year_range.split("-"))
            year_range = (y0, y1)

        print("\n[3/4] 產生月度分布儀表板 seasonal.html ...")
        from .dashboard.seasonal_dashboard import build_seasonal_dashboard
        out = build_seasonal_dashboard(df, year_range=year_range)
        print(f"  → {out}")

        print("\n[4/4] 產生泌乳曲線儀表板 lactation.html ...")
        from .dashboard.lactation_dashboard import build_lactation_dashboard
        out = build_lactation_dashboard(df, year_range=year_range)
        print(f"  → {out}")

    # === 異常告警摘要 ===
    print("\n" + "=" * 60)
    print(f"完成！總耗時 {time.time() - t_start:.0f} 秒")
    print("=" * 60)

    # 自動跑 diagnose
    print("\n[異常告警摘要]")
    args2 = type("args", (), {})()
    cmd_diagnose(args2)


def _interactive_forecast_ts(args):
    """forecast-ts 互動式設定。"""
    import sys
    if not sys.stdin.isatty() or getattr(args, 'non_interactive', False):
        return
    explicit = (getattr(args, 'horizon_months', None) is not None or
                getattr(args, 'reference_date', None) is not None or
                getattr(args, 'national_only', False))
    if explicit:
        return

    # 載入快取看資料
    try:
        from .data import load_combined
        df = load_combined(config.SNAPSHOT_DIR / "_cache.pkl")
        latest_str = df["sample_date"].max().date().isoformat()
    except Exception:
        latest_str = "(自動)"

    print()
    print("="*60)
    print("📈 純時間序列預測設定")
    print("="*60)
    print(f"資料目前最新日期: {latest_str}")
    print(f"預設預測時程: 12 個月")
    print(f"預設區域: 全國 + 北/中/南/東")
    print()

    # 預測時程
    print("【預測時程選擇】")
    print("  1) 12 個月（標準，預設）")
    print("  2) 6 個月（短期供需）")
    print("  3) 18 個月")
    print("  4) 24 個月（跨年規劃）")
    print("  5) 自訂月數")
    choice = input("請選 [1]: ").strip() or "1"
    horizon_map = {"1": 12, "2": 6, "3": 18, "4": 24}
    if choice == "5":
        try:
            args.horizon_months = int(input("輸入月數: ").strip())
        except ValueError:
            args.horizon_months = 12
    else:
        args.horizon_months = horizon_map.get(choice, 12)

    # 基準日
    print()
    print("【基準日選擇】（預測從哪天往後算）")
    print("  1) 自動（用最新資料日，最常用，預設）")
    print("  2) 強制設為某日（What-if 回顧）")
    choice = input("請選 [1]: ").strip() or "1"
    if choice == "2":
        d = input("輸入基準日 (YYYY-MM-DD): ").strip()
        if d:
            args.reference_date = d

    # 範圍
    print()
    print("【預測範圍選擇】")
    print("  1) 全國 + 北/中/南/東 4 區（預設、推薦）")
    print("  2) 只跑全國（最快、~3 秒）")
    choice = input("請選 [1]: ").strip() or "1"
    if choice == "2":
        args.national_only = True

    # 儀表板（如果還沒指定）
    if not getattr(args, 'dashboard', False):
        print()
        print("【儀表板輸出】")
        print("  1) 是、跑完後產生 timeseries.html（預設、推薦）")
        print("  2) 不要、只存 snapshot")
        choice = input("請選 [1]: ").strip() or "1"
        if choice == "1":
            args.dashboard = True

    # Holdout backtest（只有在會產儀表板時才問）
    if getattr(args, 'dashboard', False):
        backtest_cache = config.SNAPSHOT_DIR / "_holdout_backtest.json"
        has_cache = backtest_cache.exists()
        print()
        print("【Holdout backtest 設定】")
        print("（這是讓主管機關信任預測精度的關鍵驗證、跑 4 年 × 5 模型約 1-2 分鐘）")
        if has_cache:
            print()
            print(f"✓ 已有 backtest 快取：{backtest_cache.name}")
            print("  1) 用快取（最快、預設）")
            print("  2) 強制重跑 backtest（資料更新後用）")
            print("  3) 自訂 backtest 年份")
            print("  4) 跳過、儀表板不顯示 backtest 區塊")
            choice = input("請選 [1]: ").strip() or "1"
        else:
            print()
            print("⚠ 還沒有 backtest 快取、首次執行需要 1-2 分鐘")
            print("  1) 跑（預設、推薦）")
            print("  2) 自訂 backtest 年份")
            print("  3) 跳過、儀表板不顯示 backtest 區塊")
            choice = input("請選 [1]: ").strip() or "1"
            # Reindex
            if choice == "1": choice = "2"  # 對應「強制重跑」
            elif choice == "2": choice = "3"  # 對應「自訂年份」
            elif choice == "3": choice = "4"  # 對應「跳過」

        if choice == "2":
            args.rerun_backtest = True
            args.backtest_years = [2021, 2022, 2023, 2024]
        elif choice == "3":
            print()
            print("可選年份：2018-2024（需要該年和前一年的官方真值）")
            yr_str = input("輸入逗號分隔年份 [2021,2022,2023,2024]: ").strip()
            if yr_str:
                try:
                    args.backtest_years = [int(y.strip()) for y in yr_str.split(",")]
                except ValueError:
                    args.backtest_years = [2021, 2022, 2023, 2024]
            else:
                args.backtest_years = [2021, 2022, 2023, 2024]
            args.rerun_backtest = True
        elif choice == "4":
            args.skip_backtest = True

    # 進階模型選項
    has_advanced = (getattr(args, 'with_cohort', False)
                    or getattr(args, 'with_neural', False))
    if getattr(args, 'dashboard', False) and not has_advanced:
        print()
        print("【進階模型選項 / Advanced Model Options】")
        print("（標準時序模型已含 5 個。進階選項提供額外的交叉驗證）")
        print("  1) 標準（不加進階模型，預設）")
        print("  2) 加 Cohort 結構模型（牛數 × 單頭日產乳）")
        print("  3) 加 NeuralProphet 神經網路")
        print("  4) 全部都加（Cohort + NeuralProphet）")
        choice = input("請選 [1]: ").strip() or "1"
        if choice == "2":
            args.with_cohort = True
        elif choice == "3":
            args.with_neural = True
        elif choice == "4":
            args.with_cohort = True
            args.with_neural = True

    # SF 方法選擇（涵蓋率還原係數）
    if (getattr(args, 'dashboard', False)
            and getattr(args, 'sf_method', 'farms') == 'farms'
            and not getattr(args, 'non_interactive', False)):
        print()
        print("【SF 涵蓋率還原方法 / Scale Factor Method】")
        print("（決定如何把 DHI 樣本還原成全國估計、會影響全國預測值）")
        print("  1) 場數比（官方場數 / DHI 場數、預設、4 年回測 MAPE 約 10%）")
        print("  2) 牛口比（官方產乳牛 / DHI 產乳牛、4 年回測 MAPE 約 21%）")
        print("  3) 50/50 混合（兩者加權平均、4 年回測 MAPE 約 13%）")
        print("  4) 跑 backtest 對比後再選（推薦：把 --rerun-backtest 同時打開）")
        choice = input("請選 [1]: ").strip() or "1"
        if choice == "2":
            args.sf_method = "cows"
            print("  ⚠️ 牛口比歷史驗證較差、確認使用？(此選項主要供研究)")
            if input("  繼續？[y/N]: ").strip().lower() != "y":
                args.sf_method = "farms"
        elif choice == "3":
            args.sf_method = "mixed"
        elif choice == "4":
            args.sf_method = "farms"  # 暫用 farms、結果會在 backtest 報告中對比

    # 資料完整性檢查
    print()
    print("【執行前資料檢查】")
    bt_years_for_audit = (getattr(args, 'backtest_years', None)
                            if getattr(args, 'rerun_backtest', False)
                            else None)
    if (getattr(args, 'dashboard', False)
            and not getattr(args, 'skip_backtest', False)
            and not bt_years_for_audit):
        bt_years_for_audit = [2021, 2022, 2023, 2024]
    try:
        from .data.data_audit import (audit_data_for_forecast,
                                        print_audit_report)
        audit = audit_data_for_forecast(
            reference_date=args.reference_date,
            horizon_months=args.horizon_months,
            backtest_years=bt_years_for_audit)
        print_audit_report(audit)
        if not audit["ready"]:
            print()
            if input("資料不齊、仍要繼續？[y/N]: ").strip().lower() != "y":
                print("已取消")
                sys.exit(0)
    except Exception as e:
        print(f"⚠️ 資料檢查失敗：{e}（仍會繼續）")

    # 預覽
    print()
    print("="*60)
    print("即將執行：")
    print(f"  預測時程: {args.horizon_months} 個月")
    print(f"  基準日:   {args.reference_date or '自動'}")
    print(f"  範圍:     {'只跑全國' if args.national_only else '全國 + 4 區域'}")
    print(f"  儀表板:   {'是' if getattr(args,'dashboard',False) else '否'}")
    if getattr(args, 'dashboard', False):
        sf_label = {
            "farms": "場數比（預設）",
            "cows":  "牛口比",
            "mixed": "場數+牛口 50/50"
        }.get(getattr(args, 'sf_method', 'farms'), 'farms')
        print(f"  SF 方法:  {sf_label}")
        if getattr(args, 'skip_backtest', False):
            print(f"  Backtest: 跳過")
        elif getattr(args, 'rerun_backtest', False):
            yrs = getattr(args, 'backtest_years', [2021,2022,2023,2024])
            print(f"  Backtest: 跑 {yrs}")
        else:
            print(f"  Backtest: 用快取")
    print("="*60)
    if input("確認執行？[Y/n]: ").strip().lower() == "n":
        print("已取消")
        sys.exit(0)


def cmd_forecast_ts(args):
    """純時間序列預測（不跑場別 bottom-up，秒級完成）"""
    from .forecast.timeseries_pipeline import run_timeseries_only
    from .dashboard.timeseries_dashboard import build_timeseries_dashboard

    # 解析 --backtest-years（CLI 字串 → 整數 list）
    if isinstance(getattr(args, 'backtest_years', None), str):
        try:
            args.backtest_years = [int(y.strip())
                                    for y in args.backtest_years.split(",")]
        except ValueError:
            args.backtest_years = None

    _interactive_forecast_ts(args)

    print(f"\n[i] 純時間序列預測模式...")
    res = run_timeseries_only(
        reference_date=args.reference_date,
        horizon_months=args.horizon_months,
        include_regions=not args.national_only,
        with_neural=getattr(args, "with_neural", False),
    )
    print(f"\n[OK] snapshot {res['snapshot_id']}")
    print(f"     預測月數: {res['manifest']['config']['horizon_months']}")
    print(f"     基準日: {res['manifest']['config']['reference_date']}")
    print(f"     涵蓋區域: {res['manifest']['config']['regions']}")
    print(f"     耗時: {res['manifest']['elapsed_seconds']:.1f}s")

    if args.dashboard:
        out = build_timeseries_dashboard(
            res["snapshot_id"],
            rerun_backtest=getattr(args, "rerun_backtest", False),
            backtest_years=getattr(args, "backtest_years", None),
            skip_backtest=getattr(args, "skip_backtest", False),
            with_cohort=getattr(args, "with_cohort", False),
            with_neural=getattr(args, "with_neural", False),
            sf_method=getattr(args, "sf_method", "farms"))
        print(f"     dashboard: {out}")


def cmd_diagnose(args):
    snaps = sorted(config.SNAPSHOT_DIR.glob("2*"), reverse=True)
    if not snaps:
        print("No snapshots yet.")
        return
    latest = snaps[0]
    with open(latest / "results.pkl", "rb") as f:
        results = pickle.load(f)
    print(f"=== Anomaly diagnostics from {latest.name} ===")
    n_alert, n_warn = 0, 0
    for r in results:
        if "error" in r: continue
        anom = r.get("anomaly", {})
        if anom.get("severity") == "alert":
            n_alert += 1
            print(f"  [ALERT] {r['farm_id']}: {anom['message']}")
            for b in anom["breach_months"][:3]:
                print(f"     {b['month']}: {b['err_pct']:+.1f}%")
        elif anom.get("severity") == "warning":
            n_warn += 1
            print(f"  [warn]  {r['farm_id']}: {anom['message']}")
    if n_alert == 0 and n_warn == 0:
        print("  No anomalies detected.")
    print(f"\nSummary: {n_alert} alerts, {n_warn} warnings")

def main():
    parser = argparse.ArgumentParser(prog="milkfc")
    parser.add_argument("--version", action="version",
                        version=f"milkfc {__version__} (model {__model_version__})")
    sub = parser.add_subparsers(dest="cmd")

    p_run = sub.add_parser("run", help="跑完整 pipeline")
    p_run.add_argument("--farms", help="逗號分隔場號 (預設全部)")
    p_run.add_argument("--mode", choices=["backtest","production","combined"],
                       default="combined",
                       help="combined=同時跑驗證+預測 (預設) / production=只預測未來 / backtest=只跑歷史驗證")
    p_run.add_argument("--train-end", default=None,
                       help="僅 backtest 模式有用，預設 2023-12-31")
    p_run.add_argument("--target-year", type=int, default=None,
                       help="僅 backtest 模式有用，預設 2024")
    p_run.add_argument("--no-backtest", action="store_true")
    p_run.add_argument("--n-sim", type=int)
    p_run.add_argument("--train-window", type=int, default=None,
                       help="訓練視窗月數 (預設 48; 設了會關閉 auto-window)")
    p_run.add_argument("--no-auto-window", action="store_true",
                       help="關閉每場自動選視窗")
    p_run.add_argument("--horizon-months", type=int, default=None,
                       help="預測時程月數 (預設 12)")
    p_run.add_argument("--reference-date", default=None,
                       help="強制以某日為「現在」基準, 例如 2025-12-31")
    p_run.add_argument("--dashboard", action="store_true",
                       help="跑完後重新生成儀表板")

    sub.add_parser("validate", help="只跑資料驗證")
    sub.add_parser("reload", help="強制重新載入 DHI（上傳新年度檔後用）")

    p_ts = sub.add_parser("forecast-ts",
        help="純時間序列預測（不跑場別 bottom-up、秒級完成）")
    p_ts.add_argument("--horizon-months", type=int, default=None)
    p_ts.add_argument("--reference-date", default=None,
        help="基準日 YYYY-MM-DD")
    p_ts.add_argument("--national-only", action="store_true",
        help="只跑全國、不跑區域")
    p_ts.add_argument("--dashboard", action="store_true",
        help="跑完後產生 timeseries.html")
    p_ts.add_argument("--rerun-backtest", action="store_true",
        help="強制重跑 holdout backtest（首次執行或資料更新後用）")
    p_ts.add_argument("--backtest-years",
        help="自訂 backtest 年份，逗號分隔，例如 2022,2023,2024")
    p_ts.add_argument("--skip-backtest", action="store_true",
        help="儀表板不顯示 backtest 區塊")
    p_ts.add_argument("--with-cohort", action="store_true",
        help="進階：加入 Cohort 結構模型（牛數 × 單頭日產乳）")
    p_ts.add_argument("--with-neural", action="store_true",
        help="進階：加入 NeuralProphet 神經網路模型")
    p_ts.add_argument("--sf-method", choices=["farms", "cows", "mixed"],
        default="farms",
        help="SF 計算方法：farms=官方場數/DHI 場數（預設）、"
             "cows=官方產乳牛/DHI 產乳牛、mixed=兩者 50/50 加權")
    p_ts.add_argument("--non-interactive", action="store_true",
        help="跳過互動提示")
    sub.add_parser("status", help="顯示最近快照狀態")
    sub.add_parser("list-snaps", help="列出所有快照")
    sub.add_parser("diagnose", help="顯示異常診斷")

    p_an = sub.add_parser("analyze", help="產生描述性分析儀表板（月度分布/泌乳曲線）")
    p_an.add_argument("--view", choices=["seasonal","lactation","all"],
                       default="all",
                       help="seasonal=月度分布 / lactation=泌乳曲線 / all=兩者")
    p_an.add_argument("--year-range", help="例如 2022-2024 限制年度範圍")

    p_mo = sub.add_parser("monthly", help="月度一鍵全跑（預測+月度分布+泌乳曲線+異常告警）")
    p_mo.add_argument("--farms", help="逗號分隔場號 (預設全部)")
    p_mo.add_argument("--n-sim", type=int)
    p_mo.add_argument("--train-window", type=int, default=None)
    p_mo.add_argument("--no-auto-window", action="store_true")
    p_mo.add_argument("--horizon-months", type=int, default=None,
                      help="預測時程月數 (預設 12)")
    p_mo.add_argument("--reference-date", default=None,
                      help="強制以某日為「現在」基準, 例如 2025-12-31")
    p_mo.add_argument("--year-range", help="泌乳/月度分析的年度範圍，例如 2022-2024")
    p_mo.add_argument("--skip-analyze", action="store_true",
                      help="跳過月度分布與泌乳曲線（只跑預測）")
    p_mo.add_argument("--non-interactive", action="store_true",
                      help="跳過互動提示（cron 排程用）")

    args = parser.parse_args()
    if args.cmd == "run":
        cmd_run(args)
    elif args.cmd == "validate":
        cmd_validate(args)
    elif args.cmd == "status":
        cmd_status(args)
    elif args.cmd == "list-snaps":
        cmd_list_snaps(args)
    elif args.cmd == "diagnose":
        cmd_diagnose(args)
    elif args.cmd == "reload":
        cmd_reload(args)
    elif args.cmd == "forecast-ts":
        cmd_forecast_ts(args)
    elif args.cmd == "analyze":
        cmd_analyze(args)
    elif args.cmd == "monthly":
        cmd_monthly(args)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
