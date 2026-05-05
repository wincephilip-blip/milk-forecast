"""產生生產級儀表板 HTML（含場別下拉、單場詳情頁）"""
import pandas as pd
import numpy as np
import json
import pickle
from pathlib import Path
from .. import config

def build_dashboard(snapshot_id: str = None) -> Path:
    """從快照產生儀表板 HTML"""
    if snapshot_id is None:
        snaps = sorted(config.SNAPSHOT_DIR.glob("2*"), reverse=True)
        # 也納入 merged_*
        snaps += sorted(config.SNAPSHOT_DIR.glob("merged_*"), reverse=True)
        if not snaps:
            raise FileNotFoundError("No snapshots")
        snap_dir = snaps[0]
    else:
        snap_dir = config.SNAPSHOT_DIR / snapshot_id

    with open(snap_dir / "manifest.json") as f:
        manifest = json.load(f)
    with open(snap_dir / "results.pkl", "rb") as f:
        results = pickle.load(f)

    farm_summary = []
    fc_by_farm = {}
    farm_meta_by_id = {}
    anomaly_list = []
    all_fc_records = []

    for r in results:
        if "error" in r:
            continue
        fid = r["farm_id"]
        bt = r.get("backtest", {})
        anom = r.get("anomaly", {})
        meta = r.get("meta", {})

        fc = r["forecast"]
        fc_records = fc.to_dict(orient="records")
        fc_by_farm[fid] = fc_records
        for rec in fc_records:
            rec_copy = dict(rec)
            rec_copy["farm_id"] = fid
            all_fc_records.append(rec_copy)

        farm_meta_by_id[fid] = {
            "data_latest": r.get("data_latest"),
            "train_end": r.get("train_end"),
            "n_records": r.get("n_records"),
            "mode": r.get("mode", "backtest"),
            "n_active": meta.get("n_active_cows"),
            "growth_pct": meta.get("growth_pct_yoy", 0) * 100,
            "segment": r.get("segment", "未分類"),
            "sexed_rate": r["models"].get("sexed_semen_rate", 0) * 100,
        }

        farm_summary.append({
            "farm_id": fid,
            "n_active": meta.get("n_active_cows", 0),
            "growth_pct": meta.get("growth_pct_yoy", 0) * 100,
            "preg_rate": r["models"].get("preg_rate_overall", 0) * 100,
            "conv_rate": r["models"].get("conv_rate", 0) * 100,
            "sexed_rate": r["models"].get("sexed_semen_rate", 0) * 100,
            "segment": r.get("segment", "未分類"),
            "data_latest": r.get("data_latest"),
            "mape": bt.get("mape"),
            "bias": bt.get("bias"),
            "coverage": (bt.get("coverage", 0) or 0) * 100,
            "anomaly_severity": anom.get("severity", "normal"),
            "anomaly_msg": anom.get("message", ""),
        })
        if anom.get("severity") in ("alert", "warning"):
            anomaly_list.append({
                "farm_id": fid,
                "severity": anom["severity"],
                "message": anom["message"],
                "breaches": anom.get("breach_months", [])[:5],
            })

    # 加總視圖
    df_all = pd.DataFrame(all_fc_records)
    agg_records = []
    agg_mape, agg_bias = None, None
    if len(df_all) > 0:
        # 計算每月有幾場貢獻
        farm_count_per_month = df_all.groupby("yyyymm")["farm_id"].nunique()
        max_farms = farm_count_per_month.max()

        # 只保留 contribution >= 80% 的月份（避免 val_year 不一致造成的零點）
        # 這對應「絕大多數場都有產出該月預測」的有效月份
        threshold = max_farms * 0.8
        valid_months = farm_count_per_month[farm_count_per_month >= threshold].index.tolist()

        df_valid = df_all[df_all["yyyymm"].isin(valid_months)]

        agg = df_valid.groupby("yyyymm").agg(
            p10=("p10","sum"), p50=("p50","sum"), p90=("p90","sum"),
            n_farms=("farm_id","nunique"),
        ).reset_index()
        # actual 要用 min_count=1 才能保留全 NaN 月份為 NaN（不是 0）
        if "actual" in df_valid.columns:
            actual_sum = df_valid.groupby("yyyymm")["actual"].sum(min_count=1)
            agg["actual"] = agg["yyyymm"].map(actual_sum)
            agg["err_pct"] = np.where(
                agg["actual"].notna() & (agg["actual"] > 0),
                (agg["p50"] - agg["actual"]) / agg["actual"] * 100,
                np.nan,
            )
            mask = agg["actual"].notna() & (agg["actual"] > 0)
            if mask.sum() > 0:
                agg_mape = float(agg.loc[mask, "err_pct"].abs().mean())
                agg_bias = float(agg.loc[mask, "err_pct"].mean())
        agg = agg.where(pd.notnull(agg), None)
        agg_records = agg.to_dict(orient="records")

    df_sum = pd.DataFrame(farm_summary)
    valid = df_sum[df_sum["mape"].notna()] if "mape" in df_sum.columns else pd.DataFrame()
    kpis = {
        "n_farms": len(df_sum),
        "n_alerts": int((df_sum["anomaly_severity"]=="alert").sum()) if "anomaly_severity" in df_sum.columns else 0,
        "n_warnings": int((df_sum["anomaly_severity"]=="warning").sum()) if "anomaly_severity" in df_sum.columns else 0,
        "mape_median": float(valid["mape"].median()) if len(valid) else None,
        "mape_mean": float(valid["mape"].mean()) if len(valid) else None,
        "agg_mape": agg_mape,
        "agg_bias": agg_bias,
        "coverage_mean": float(valid["coverage"].mean()) if len(valid) else None,
    }

    # 全場最新資料時間（取最大值）
    latest_data_dates = [r.get("data_latest") for r in results
                         if r.get("data_latest")]
    overall_latest_data = max(latest_data_dates) if latest_data_dates else "n/a"

    # 涵蓋率校正 + 區域加總
    coverage_info = None
    region_aggregates = {}
    try:
        from ..data import load_combined
        from ..calibration import (compute_coverage_rates, aggregate_by_region,
                                     compute_monthly_scale_factors)
        df_dhi = load_combined(config.SNAPSHOT_DIR / "_cache.pkl")
        coverage_info = compute_coverage_rates(df_dhi)

        # 整理 forecast_with_farm 用於區域加總
        farm_to_county = coverage_info["farm_to_county"]
        if all_fc_records:
            df_fc_full = pd.DataFrame(all_fc_records)
            for scope in ["macro","county"]:
                ag = aggregate_by_region(df_fc_full, farm_to_county, scope=scope)
                ag = ag.where(pd.notnull(ag), None)
                region_aggregates[scope] = ag.to_dict(orient="records")

        # === 月度動態 scale factor ===
        all_months = sorted({r["yyyymm"] for r in agg_records})
        monthly_sf = compute_monthly_scale_factors(df_dhi, all_months)
        coverage_info["monthly_scale_factors"] = monthly_sf

        # 全國校正：每月用該月的 scale factor
        calibrated_records = []
        for r in agg_records:
            cr = dict(r)
            sf = monthly_sf.get(r["yyyymm"], {}).get(
                "scale_factor",
                coverage_info["scale_factor_national"])
            cr["scale_factor"] = sf
            for k in ["p10","p50","p90","actual"]:
                if cr.get(k) is not None:
                    cr[k] = cr[k] * sf
            calibrated_records.append(cr)
        coverage_info["calibrated_aggregate"] = calibrated_records
    except Exception as e:
        import traceback; traceback.print_exc()
        coverage_info = {"error": str(e)}

    # 載入 Top-Down 時序預測（如果有）
    topdown = None
    try:
        topdown_file = snap_dir / "topdown_forecast.json"
        if topdown_file.exists():
            with open(topdown_file) as f:
                topdown = json.load(f)
    except Exception:
        pass

    payload = {
        "manifest": manifest,
        "kpis": kpis,
        "farms": farm_summary,
        "fc_by_farm": fc_by_farm,
        "farm_meta_by_id": farm_meta_by_id,
        "aggregated": agg_records,
        "anomalies": anomaly_list,
        "overall_latest_data": overall_latest_data,
        "is_production_mode": manifest.get("config",{}).get("mode") == "production",
        "coverage": coverage_info,
        "region_aggregates": region_aggregates,
        "topdown": topdown,
    }

    html = _render_html(payload)
    out = config.DASHBOARD_OUT
    out.write_text(html, encoding="utf-8")
    return out


def _render_html(p: dict) -> str:
    is_prod = p["is_production_mode"]
    mode_label = "生產模式（未來預測）" if is_prod else "稽核模式（歷史回測）"
    return f"""<!DOCTYPE html>
<html lang="zh-Hant"><head><meta charset="UTF-8">
<title>DHI 乳量預測儀表板</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>{_CSS}</style>
</head><body>

<header>
  <h1>📊 DHI 乳量預測儀表板</h1>
  <div class="meta">
    <span class="badge mode-badge">{mode_label}</span>
    資料更新至 <code>{p['overall_latest_data']}</code> ·
    {p['kpis']['n_farms']} 場 ·
    快照 <code>{p['manifest']['snapshot_id']}</code> ·
    模型 <code>{p['manifest']['model_version']}</code>
  </div>
</header>

<nav class="topnav">
  <a href="dashboard.html" class="active">📊 預測</a>
  <a href="timeseries.html">📈 時間序列</a>
  <a href="seasonal.html">📅 月度分布</a>
  <a href="lactation.html">🐄 泌乳曲線</a>
  <span class="unit-picker">單位:
    <select id="sel_unit">
      <option value="kg">kg</option>
      <option value="ton">公噸 (×1,000)</option>
      <option value="wton">萬公噸 (×10,000,000)</option>
      <option value="kton">千噸 (×1,000,000)</option>
    </select>
  </span>
</nav>

<section class="kpis">
  <div class="kpi"><div class="label">場數</div>
    <div class="value">{p['kpis']['n_farms']}</div></div>
  <div class="kpi {'bad' if p['kpis']['n_alerts'] else 'good'}">
    <div class="label">異常告警</div>
    <div class="value">{p['kpis']['n_alerts']}</div>
    <div class="footnote">+ {p['kpis']['n_warnings']} 警告</div></div>
  <div class="kpi"><div class="label">場級 MAPE 中位</div>
    <div class="value">{_fmt(p['kpis']['mape_median'])}{'%' if p['kpis']['mape_median'] is not None else ''}</div>
    <div class="footnote">{'生產模式無回測' if is_prod and p['kpis']['mape_median'] is None else ''}</div></div>
  <div class="kpi good"><div class="label">加總後 MAPE</div>
    <div class="value">{_fmt(p['kpis']['agg_mape'])}{'%' if p['kpis']['agg_mape'] is not None else ''}</div>
    <div class="footnote">產業尺度</div></div>
</section>

{_render_anomaly_panel(p['anomalies'])}

<section class="card primary-section">
  <h2>單場詳情查詢</h2>
  <div class="farm-picker">
    <label for="sel">選擇牧場：</label>
    <select id="sel"></select>
    <span class="data-time">資料時間: <code id="data_latest_inline"></code></span>
  </div>

  <div id="farm_meta_card" class="meta-card"></div>

  <div class="chart-wrap"><canvas id="farm_chart"></canvas></div>

  <h3 style="margin-top: 24px;">月度乳量預測明細</h3>
  <div class="table-wrap"><table id="detail_tbl">
    <thead><tr>
      <th>月份</th>
      <th>狀態</th>
      <th>預測 P50 (kg)</th>
      <th>P10–P90 區間 (kg)</th>
      <th>實際 (kg)</th>
      <th>誤差</th>
    </tr></thead>
    <tbody></tbody>
  </table></div>

  <p class="note" id="detail_summary"></p>
</section>

<section class="card">
  <h2>跨場加總（產業尺度）</h2>
  <div class="picker">
    <label>視角:</label>
    <select id="sel_scope">
      <option value="dhi">DHI 加總（直接相加 130 場）</option>
      <option value="national_calibrated">全國估計（用官方涵蓋率外推 ×{_fmt(p['coverage'].get('scale_factor_national',1.0),2) if p.get('coverage') else 1.0}）</option>
      <option value="macro">按 macro 區域（北/中/南/東）</option>
      <option value="county">按縣市</option>
    </select>
    <select id="sel_region" style="display:none;">
      <option value="">（請選區域）</option>
    </select>
  </div>
  <div class="chart-wrap"><canvas id="agg_chart"></canvas></div>
  <p class="note" id="agg_note">所有場預測加總視圖。{'尚無歷史對照可比 (生產模式)' if is_prod else f'加總後 MAPE = {_fmt(p["kpis"]["agg_mape"])}%、bias = {_fmt(p["kpis"]["agg_bias"])}%'}</p>
</section>

<section class="card" id="coverage_section">
  <h2>DHI 全國涵蓋率（官方季報校正）</h2>
  <div id="coverage_info"></div>
</section>

<section class="card" id="topdown_section">
  <h2>Top-Down 時間序列模型對比</h2>
  <p class="note">直接把全國月乳量當時間序列、用經典統計方法預測，與 Bottom-Up（場別累加）互相驗證。</p>
  <div class="chart-wrap" style="height:380px;"><canvas id="td_chart"></canvas></div>
  <div id="td_summary"></div>
</section>

<section class="card">
  <h2>各場概況</h2>
  <div class="table-wrap"><table id="farm_tbl">
    <thead><tr><th>場</th><th>狀態</th><th>分類</th><th>活躍頭數</th><th>YoY 成長</th>
      <th>配種成功</th><th>性控%</th><th>後備轉換</th>
      {'<th>MAPE</th><th>偏差</th>' if not is_prod else ''}
      <th>資料時間</th></tr></thead>
    <tbody></tbody></table></div>
</section>

<section class="card">
  <h2>運行紀錄</h2>
  <pre>{json.dumps(p['manifest'], indent=2, ensure_ascii=False, default=str)}</pre>
</section>

<script>
const D = {json.dumps(p, default=str)};
const IS_PROD = {('true' if is_prod else 'false')};

// === 單位切換 ===
const UNIT_INFO = {{
  kg:   {{ divisor: 1, label: 'kg', precision: 0 }},
  ton:  {{ divisor: 1000, label: '公噸', precision: 1 }},
  wton: {{ divisor: 10000000, label: '萬公噸', precision: 3 }},
  kton: {{ divisor: 1000000, label: '千噸', precision: 2 }},
}};
let CUR_UNIT = 'kg';
function unit() {{ return UNIT_INFO[CUR_UNIT]; }}
function fmt_v(v) {{
  if (v == null) return '—';
  return (v / unit().divisor).toFixed(unit().precision);
}}
function fmt_int(v) {{
  if (v == null) return '—';
  const conv = v / unit().divisor;
  return conv.toLocaleString(undefined, {{
    minimumFractionDigits: unit().precision,
    maximumFractionDigits: unit().precision,
  }});
}}
function unit_label() {{ return `月乳量 (${{unit().label}})`; }}

document.getElementById('sel_unit')?.addEventListener('change', e => {{
  CUR_UNIT = e.target.value;
  // Re-render everything that depends on units
  if (typeof renderFarm === 'function') renderFarm();
  if (typeof renderAggChart === 'function') renderAggChart();
}});

// === Farm dropdown ===
const sel = document.getElementById('sel');
// 排序: 異常場優先、然後字母排序
const sortedFarms = [...D.farms].sort((a,b) => {{
  const sevOrder = {{alert: 0, warning: 1, normal: 2}};
  if (sevOrder[a.anomaly_severity] !== sevOrder[b.anomaly_severity])
    return sevOrder[a.anomaly_severity] - sevOrder[b.anomaly_severity];
  return a.farm_id.localeCompare(b.farm_id);
}});
sortedFarms.forEach(f => {{
  const o = document.createElement('option');
  o.value = f.farm_id;
  const prefix = f.anomaly_severity === 'alert' ? '⚠ ' :
                 f.anomaly_severity === 'warning' ? '! ' : '';
  o.textContent = `${{prefix}}場 ${{f.farm_id}} (${{f.n_active}} 頭)`;
  sel.appendChild(o);
}});

// === Per-farm rendering ===
let farmChart = null;
function renderFarm() {{
  try {{
  const fid = sel.value;
  const fc = D.fc_by_farm[fid] || [];
  const meta = D.farm_meta_by_id[fid] || {{}};
  const f = D.farms.find(x => x.farm_id === fid) || {{}};

  document.getElementById('data_latest_inline').textContent = meta.data_latest || '?';

  // Meta card（全部用 ?? / ?. 防 null）
  const fmt_pct = (v) => (v == null ? '—' : Number(v).toFixed(1) + '%');
  const fmt_int = (v) => (v == null ? '—' : Number(v).toLocaleString());
  document.getElementById('farm_meta_card').innerHTML = `
    <div class="meta-row">
      <div class="meta-cell"><span class="lbl">資料更新至</span>
        <span class="val">${{meta.data_latest || '?'}}</span></div>
      <div class="meta-cell"><span class="lbl">活躍頭數</span>
        <span class="val">${{meta.n_active || '?'}}</span></div>
      <div class="meta-cell"><span class="lbl">YoY 成長</span>
        <span class="val">${{fmt_pct(meta.growth_pct)}}</span></div>
      <div class="meta-cell"><span class="lbl">紀錄筆數</span>
        <span class="val">${{fmt_int(meta.n_records)}}</span></div>
      <div class="meta-cell"><span class="lbl">異常狀態</span>
        <span class="val">${{f.anomaly_severity === 'alert' ? '⚠ 告警' :
                            f.anomaly_severity === 'warning' ? '! 警告' : '✓ 正常'}}</span></div>
    </div>`;

  // Chart - 單位換算
  const conv = v => v == null ? null : v / unit().divisor;
  if (farmChart) farmChart.destroy();
  farmChart = new Chart(document.getElementById('farm_chart'), {{
    type: 'line',
    data: {{labels: fc.map(r => r.yyyymm),
      datasets: [
        {{label: 'P90', data: fc.map(r => conv(r.p90)), borderColor: 'transparent',
          backgroundColor: 'rgba(42,77,105,0.18)', fill: '+1', pointRadius: 0}},
        {{label: 'P10', data: fc.map(r => conv(r.p10)), borderColor: 'transparent',
          backgroundColor: 'rgba(42,77,105,0.18)', fill: false, pointRadius: 0}},
        {{label: '預測 P50', data: fc.map(r => conv(r.p50)), borderColor: '#2a4d69',
          borderWidth: 2.5, pointRadius: 3, tension: 0.3}},
        {{label: '實際', data: fc.map(r => r.actual ? conv(r.actual) : null),
          borderColor: '#d05a3c',
          borderWidth: 2.5, pointRadius: 4, borderDash: [4,4], tension: 0.3,
          spanGaps: false}}
      ]}},
    options: {{responsive: true, maintainAspectRatio: false,
      plugins: {{legend: {{position: 'top'}},
        tooltip: {{mode: 'index', intersect: false}}}},
      scales: {{y: {{title: {{display: true, text: unit_label()}}}}}}
    }}
  }});

  // Detail table
  const tb = document.querySelector('#detail_tbl tbody');
  tb.innerHTML = '';

  // 把 fc 分兩段：歷史驗證 + 未來預測
  const past = fc.filter(r => r.actual != null && !isNaN(r.actual));
  const future = fc.filter(r => r.actual == null || isNaN(r.actual));

  let past_p50 = 0, past_p10 = 0, past_p90 = 0, past_actual = 0, sum_abs_err = 0;
  let fut_p50 = 0, fut_p10 = 0, fut_p90 = 0;

  // 渲染過去（驗證）部分
  if (past.length > 0) {{
    tb.insertAdjacentHTML('beforeend',
      `<tr class="row-section"><td colspan="6">━━━ 歷史驗證 (${{past.length}} 個月) ━━━</td></tr>`);
    past.forEach(r => {{
      const e = (r.p50 - r.actual) / r.actual * 100;
      const errCls = Math.abs(e) <= 10 ? 'good' : Math.abs(e) <= 20 ? 'warn' : 'bad';
      sum_abs_err += Math.abs(e);
      past_p50 += r.p50; past_p10 += r.p10; past_p90 += r.p90;
      past_actual += r.actual;
      tb.insertAdjacentHTML('beforeend', `
        <tr class="row-past">
          <td><b>${{r.yyyymm}}</b></td>
          <td><span class="status status-actual">已驗證</span></td>
          <td class="num">${{fmt_int(r.p50)}}</td>
          <td class="num small">${{fmt_int(r.p10)}} – ${{fmt_int(r.p90)}}</td>
          <td class="num">${{fmt_int(r.actual)}}</td>
          <td class="num"><span class="${{errCls}}">${{e > 0 ? '+' : ''}}${{e.toFixed(1)}}%</span></td>
        </tr>`);
    }});
    const validation_mape = sum_abs_err / past.length;
    const mapeCls = validation_mape <= 10 ? 'good' : validation_mape <= 20 ? 'warn' : 'bad';
    tb.insertAdjacentHTML('beforeend', `
      <tr class="row-total">
        <td><b>驗證合計</b></td>
        <td>${{past.length}} 個月</td>
        <td class="num"><b>${{fmt_int(past_p50)}}</b></td>
        <td class="num small">${{fmt_int(past_p10)}} – ${{fmt_int(past_p90)}}</td>
        <td class="num">${{fmt_int(past_actual)}}</td>
        <td class="num"><span class="${{mapeCls}}"><b>MAPE ${{validation_mape.toFixed(1)}}%</b></span></td>
      </tr>`);
  }}

  // 渲染未來預測部分
  if (future.length > 0) {{
    tb.insertAdjacentHTML('beforeend',
      `<tr class="row-section"><td colspan="6">━━━ 未來預測 (${{future.length}} 個月) ━━━</td></tr>`);
    future.forEach(r => {{
      fut_p50 += r.p50; fut_p10 += r.p10; fut_p90 += r.p90;
      tb.insertAdjacentHTML('beforeend', `
        <tr class="row-future">
          <td><b>${{r.yyyymm}}</b></td>
          <td><span class="status status-future">預測</span></td>
          <td class="num">${{fmt_int(r.p50)}}</td>
          <td class="num small">${{fmt_int(r.p10)}} – ${{fmt_int(r.p90)}}</td>
          <td class="num">—</td>
          <td class="num">—</td>
        </tr>`);
    }});
    tb.insertAdjacentHTML('beforeend', `
      <tr class="row-total">
        <td><b>預測合計</b></td>
        <td>${{future.length}} 個月</td>
        <td class="num"><b>${{fmt_int(fut_p50)}}</b></td>
        <td class="num small">${{fmt_int(fut_p10)}} – ${{fmt_int(fut_p90)}}</td>
        <td class="num">—</td>
        <td class="num">—</td>
      </tr>`);
  }}

  // 摘要
  const validation_mape = past.length > 0 ? sum_abs_err / past.length : null;
  let summary = `場 ${{fid}} | 未來 ${{future.length}} 月預測總量 ${{Math.round(fut_p50/1000).toLocaleString()}} 噸 (P10–P90: ${{Math.round(fut_p10/1000).toLocaleString()}}–${{Math.round(fut_p90/1000).toLocaleString()}} 噸)`;
  if (validation_mape != null) summary += ` | 過去 ${{past.length}} 月驗證 MAPE = ${{validation_mape.toFixed(1)}}%`;
  if (n_with_actual > 0) {{
    summary += ` | 已驗證 ${{n_with_actual}} 月 MAPE = ${{avgErr.toFixed(1)}}%`;
  }}
  if (f.anomaly_msg) summary += ` | 異常: ${{f.anomaly_msg}}`;
  document.getElementById('detail_summary').textContent = summary;
  }} catch(err) {{ console.error('renderFarm 失敗:', err); }}
}}
sel.addEventListener('change', renderFarm);
renderFarm();

// === Aggregate chart with multi-scope support ===
try {{
const sel_scope = document.getElementById('sel_scope');
const sel_region = document.getElementById('sel_region');
let aggChart = null;

function getAggData() {{
  const scope = sel_scope.value;
  if (scope === 'dhi') return D.aggregated;
  if (scope === 'national_calibrated') {{
    return D.coverage?.calibrated_aggregate || D.aggregated;
  }}
  if (scope === 'macro' || scope === 'county') {{
    const reg = sel_region.value;
    if (!reg) return [];
    return (D.region_aggregates[scope] || []).filter(r => r.region === reg);
  }}
  return D.aggregated;
}}

function refreshRegionOptions() {{
  const scope = sel_scope.value;
  if (scope === 'macro' || scope === 'county') {{
    sel_region.style.display = '';
    sel_region.innerHTML = '';
    const regs = [...new Set((D.region_aggregates[scope]||[]).map(r => r.region))].sort();
    regs.forEach(r => {{
      const o = document.createElement('option');
      o.value = r; o.textContent = r;
      sel_region.appendChild(o);
    }});
  }} else {{
    sel_region.style.display = 'none';
  }}
}}

function renderAggChart() {{
  const data = getAggData();
  const scope = sel_scope.value;
  if (aggChart) aggChart.destroy();

  let title_suffix = '';
  if (scope === 'national_calibrated') {{
    title_suffix = ` (×${{(D.coverage?.scale_factor_national || 1).toFixed(2)}} 校正)`;
  }} else if (scope === 'macro') {{
    title_suffix = ` - ${{sel_region.value}}`;
  }} else if (scope === 'county') {{
    title_suffix = ` - ${{sel_region.value}}`;
  }}

  const cv = v => v == null ? null : v / unit().divisor;
  aggChart = new Chart(document.getElementById('agg_chart'), {{
    type: 'line',
    data: {{
      labels: data.map(r => r.yyyymm),
      datasets: [
        {{label: 'P90', data: data.map(r => cv(r.p90)), borderColor: 'transparent',
          backgroundColor: 'rgba(42,77,105,0.15)', fill: '+1', pointRadius: 0}},
        {{label: 'P10', data: data.map(r => cv(r.p10)), borderColor: 'transparent',
          backgroundColor: 'rgba(42,77,105,0.15)', fill: false, pointRadius: 0}},
        {{label: '預測 P50' + title_suffix, data: data.map(r => cv(r.p50)),
          borderColor: '#2a4d69', borderWidth: 2.5, pointRadius: 3, tension: 0.3}},
        {{label: '實際', data: data.map(r => cv(r.actual)),
          borderColor: '#d05a3c', borderWidth: 2.5, pointRadius: 3,
          borderDash: [4,4], tension: 0.3, spanGaps: false}}
      ]
    }},
    options: {{responsive: true, maintainAspectRatio: false,
      plugins: {{legend: {{position: 'top'}}}},
      scales: {{y: {{title: {{display: true, text: unit_label()}}}}}}
    }}
  }});

  // 摘要 - 期間總乳量
  const note = document.getElementById('agg_note');

  // 拆成「過去（有實際）」與「未來（純預測）」分別總計
  const past = data.filter(r => r.actual != null && !isNaN(r.actual));
  const future = data.filter(r => r.actual == null || isNaN(r.actual));
  const past_p50 = past.reduce((s,r) => s + (r.p50||0), 0);
  const past_actual = past.reduce((s,r) => s + (r.actual||0), 0);
  const fut_p50 = future.reduce((s,r) => s + (r.p50||0), 0);
  const fut_p10 = future.reduce((s,r) => s + (r.p10||0), 0);
  const fut_p90 = future.reduce((s,r) => s + (r.p90||0), 0);
  const total_p50 = past_p50 + fut_p50;

  let scope_label = scope === 'national_calibrated'
    ? `全國估計（×${{(D.coverage?.scale_factor_national || 1).toFixed(2)}} 校正）`
    : scope === 'dhi'
      ? 'DHI 加總'
      : `${{sel_region.value || ''}} 區域`;

  let html = `<div class="period-totals">`;
  html += `<div class="pt-title">📊 ${{scope_label}} - 期間總乳量</div>`;
  html += `<table class="pt-table">`;
  html += `<thead><tr><th>期間</th><th>P50 預測</th><th>P10–P90</th>
            <th>實際</th><th>誤差</th></tr></thead><tbody>`;
  if (past.length > 0) {{
    const err = past_actual > 0 ? (past_p50-past_actual)/past_actual*100 : 0;
    const errCls = Math.abs(err)<=5 ? 'good' : Math.abs(err)<=10 ? 'warn' : 'bad';
    html += `<tr><td><b>歷史驗證 (${{past.length}}月)</b></td>
      <td class="num">${{fmt_int(past_p50)}}</td>
      <td>—</td>
      <td class="num">${{fmt_int(past_actual)}}</td>
      <td class="num"><span class="${{errCls}}">${{err>0?'+':''}}${{err.toFixed(1)}}%</span></td>
      </tr>`;
  }}
  if (future.length > 0) {{
    html += `<tr><td><b>未來預測 (${{future.length}}月)</b></td>
      <td class="num"><b>${{fmt_int(fut_p50)}}</b></td>
      <td class="num small">${{fmt_int(fut_p10)}} – ${{fmt_int(fut_p90)}}</td>
      <td>—</td><td>—</td>
      </tr>`;
  }}
  html += `<tr class="pt-total"><td><b>合計 (${{data.length}}月)</b></td>
    <td class="num"><b>${{fmt_int(total_p50)}}</b></td>
    <td>—</td><td>—</td><td>—</td></tr>`;
  html += `</tbody></table>`;

  // MAPE 說明
  if (scope === 'dhi' && D.kpis.agg_mape) {{
    html += `<div class="pt-foot">DHI 直接加總、不含外推。歷史驗證 MAPE = ${{D.kpis.agg_mape.toFixed(1)}}%</div>`;
  }} else if (scope === 'national_calibrated' && D.coverage) {{
    html += `<div class="pt-foot">DHI 涵蓋率 ${{(D.coverage.national.rate*100).toFixed(1)}}%、外推係數 ×${{D.coverage.scale_factor_national.toFixed(2)}}（月度動態）</div>`;
  }}
  html += `</div>`;
  note.innerHTML = html;
}}

sel_scope.addEventListener('change', () => {{
  refreshRegionOptions();
  renderAggChart();
}});
sel_region.addEventListener('change', renderAggChart);
refreshRegionOptions();
renderAggChart();
}} catch(err) {{ console.error('Aggregate chart 失敗:', err); }}

// === Top-Down 時序模型對比 ===
try {{
function renderTopDown() {{
  if (!D.topdown || !D.topdown.models) {{
    document.getElementById('td_summary').innerHTML =
      '<p class="note">Top-Down 時序模型尚未生成（可能 statsmodels/prophet 未安裝）</p>';
    return;
  }}
  const models = D.topdown.models.filter(m => m.success);
  const ensemble = D.topdown.ensemble;

  if (models.length === 0) {{
    document.getElementById('td_summary').innerHTML =
      '<p class="note">所有時序模型都失敗了</p>';
    return;
  }}

  const colors = {{
    naive_seasonal: '#999',
    stl_linear: '#1e7c3a',
    holt_winters: '#a05a00',
    sarima: '#9170b0',
    prophet: '#d05a3c',
    ensemble: '#2a4d69',
  }};

  // 收集所有月份
  const monthsSet = new Set();
  models.forEach(m => m.forecast.forEach(f => monthsSet.add(f.yyyymm)));
  if (ensemble) ensemble.forecast.forEach(f => monthsSet.add(f.yyyymm));
  const months = [...monthsSet].sort();

  const datasets = models.map(m => ({{
    label: `${{m.model}} (in-sample MAPE ${{(m.in_sample_mape || 0).toFixed(1)}}%)`,
    data: months.map(mn => {{
      const pt = m.forecast.find(f => f.yyyymm === mn);
      return pt ? pt.p50 : null;
    }}),
    borderColor: colors[m.model] || '#666',
    backgroundColor: colors[m.model] || '#666',
    borderWidth: 1.5,
    pointRadius: 2,
    tension: 0.3,
  }}));

  if (ensemble) {{
    datasets.push({{
      label: 'Ensemble (weighted)',
      data: months.map(mn => {{
        const pt = ensemble.forecast.find(f => f.yyyymm === mn);
        return pt ? pt.p50 : null;
      }}),
      borderColor: colors.ensemble,
      backgroundColor: colors.ensemble,
      borderWidth: 3,
      pointRadius: 3,
      tension: 0.3,
    }});
  }}

  if (window.tdChart) window.tdChart.destroy();
  window.tdChart = new Chart(document.getElementById('td_chart'), {{
    type: 'line',
    data: {{labels: months, datasets}},
    options: {{
      responsive: true, maintainAspectRatio: false,
      plugins: {{legend: {{position: 'top'}}}},
      scales: {{y: {{title: {{display: true, text: unit_label()}}}}}}
    }}
  }});

  // 摘要表
  let html = '<table style="margin-top:12px;font-size:13px;">';
  html += '<thead><tr><th>模型</th><th>In-Sample MAPE</th>';
  if (ensemble) html += '<th>Ensemble 權重</th>';
  html += '<th>狀態</th></tr></thead><tbody>';
  models.forEach(m => {{
    const w = ensemble?.weights?.[m.model];
    const wPct = w != null ? (w * 100).toFixed(1) + '%' : '—';
    html += `<tr><td><b>${{m.model}}</b></td>
              <td>${{(m.in_sample_mape || 0).toFixed(1)}}%</td>`;
    if (ensemble) html += `<td>${{wPct}}</td>`;
    html += `<td>✓</td></tr>`;
  }});
  // 不成功的模型
  D.topdown.models.filter(m => !m.success).forEach(m => {{
    html += `<tr><td><b>${{m.model}}</b></td>
              <td>—</td>`;
    if (ensemble) html += `<td>—</td>`;
    html += `<td style="color:#999">${{m.error || '失敗'}}</td></tr>`;
  }});
  html += '</tbody></table>';
  document.getElementById('td_summary').innerHTML = html;
}}
renderTopDown();
}} catch(err) {{ console.error('Top-Down 失敗:', err); }}

// === 涵蓋率資訊區 ===
try {{
function renderCoverage() {{
  if (!D.coverage || D.coverage.error) {{
    document.getElementById('coverage_info').innerHTML =
      '<p class="note">官方涵蓋率資料載入失敗（可能尚未放置季報 xlsx）</p>';
    return;
  }}
  const c = D.coverage;
  const n = c.national;
  let html = `
    <div class="meta-row">
      <div class="meta-cell"><span class="lbl">DHI 場數</span>
        <span class="val">${{D.kpis.n_farms}} 場</span></div>
      <div class="meta-cell"><span class="lbl">官方場數 (${{n.official_period}})</span>
        <span class="val">${{n.official_farms}} 場</span></div>
      <div class="meta-cell"><span class="lbl">DHI 活躍頭數</span>
        <span class="val">${{n.dhi_heads.toLocaleString()}}</span></div>
      <div class="meta-cell"><span class="lbl">官方產乳牛</span>
        <span class="val">${{n.official_milking.toLocaleString()}}</span></div>
      <div class="meta-cell"><span class="lbl">涵蓋率</span>
        <span class="val">${{(n.rate*100).toFixed(1)}}%</span></div>
      <div class="meta-cell"><span class="lbl">外推係數</span>
        <span class="val">×${{c.scale_factor_national.toFixed(2)}}</span></div>
    </div>
    <h3 style="margin-top:16px;font-size:14px;">按區域涵蓋率</h3>
    <table style="margin-top:8px;font-size:13px;">
      <thead><tr><th>區域</th><th>DHI 頭數</th><th>官方產乳牛</th><th>涵蓋率</th><th>外推係數</th></tr></thead>
      <tbody>`;
  Object.entries(c.by_macro).forEach(([macro, info]) => {{
    if (info.official_milking > 0) {{
      const sf = c.scale_factor_by_macro[macro];
      html += `<tr><td><b>${{macro}}</b></td><td>${{info.dhi_heads.toLocaleString()}}</td>
        <td>${{info.official_milking.toLocaleString()}}</td>
        <td>${{(info.rate*100).toFixed(1)}}%</td>
        <td>×${{sf.toFixed(2)}}</td></tr>`;
    }}
  }});
  html += `</tbody></table>`;
  document.getElementById('coverage_info').innerHTML = html;
}}
renderCoverage();
}} catch(err) {{ console.error('Coverage 失敗:', err); }}

// === Farm overview table ===
try {{
const tb = document.querySelector('#farm_tbl tbody');
sortedFarms.forEach(f => {{
  const sevMap = {{normal: '✓', warning: '!', alert: '⚠'}};
  const sevCls = {{normal: 'badge-ok', warning: 'badge-warn', alert: 'badge-bad'}};
  const mapeCls = f.mape == null ? '' : f.mape <= 12 ? 'good' : f.mape <= 20 ? 'warn' : 'bad';
  const fmt1 = (v, suf='%') => (v == null ? '—' : Number(v).toFixed(1) + suf);
  const fmt0 = (v, suf='%') => (v == null ? '—' : Number(v).toFixed(0) + suf);
  const mapeBiasCells = IS_PROD ? '' : `
    <td class="${{mapeCls}}">${{fmt1(f.mape)}}</td>
    <td>${{f.bias != null ? (f.bias > 0 ? '+' : '') + Number(f.bias).toFixed(1) + '%' : '—'}}</td>`;
  tb.insertAdjacentHTML('beforeend', `
    <tr class="clickable" data-fid="${{f.farm_id}}">
      <td><b>${{f.farm_id}}</b></td>
      <td><span class="badge ${{sevCls[f.anomaly_severity]||'badge-ok'}}">${{sevMap[f.anomaly_severity]||'?'}}</span></td>
      <td><span class="seg-tag">${{f.segment || '未分類'}}</span></td>
      <td>${{f.n_active ?? '—'}}</td>
      <td>${{fmt1(f.growth_pct)}}</td>
      <td>${{fmt0(f.preg_rate)}}</td>
      <td>${{fmt0(f.sexed_rate)}}</td>
      <td>${{fmt0(f.conv_rate)}}</td>
      ${{mapeBiasCells}}
      <td>${{f.data_latest || '?'}}</td>
    </tr>`);
}});
// 點概覽表的列 → 跳到該場
document.querySelectorAll('#farm_tbl tbody .clickable').forEach(row => {{
  row.addEventListener('click', () => {{
    sel.value = row.dataset.fid;
    renderFarm();
    document.querySelector('.primary-section').scrollIntoView({{behavior: 'smooth'}});
  }});
}});
}} catch(err) {{ console.error('Farm overview table 失敗:', err); }}

</script>
</body></html>"""

def _fmt(x, sig=1):
    if x is None: return "—"
    return f"{x:.{sig}f}"

def _render_anomaly_panel(anomalies):
    if not anomalies:
        return ('<section class="card alert-panel ok"><h2>異常告警</h2>'
                '<p>本期無告警，所有場預測誤差皆在門檻內。</p></section>')
    rows = ""
    for a in anomalies:
        cls = "alert" if a["severity"] == "alert" else "warning"
        breaches = ", ".join(f"{b['month']}: {b['err_pct']:+.0f}%"
                             for b in a["breaches"])
        rows += (f'<div class="alert-row {cls}">'
                 f'<span class="badge {"badge-bad" if cls=="alert" else "badge-warn"}">'
                 f'{a["severity"].upper()}</span> '
                 f'<b>{a["farm_id"]}</b>: {a["message"]}<br>'
                 f'<span style="color:#666;font-size:12px;">超標月份: {breaches}</span></div>')
    return f'<section class="card alert-panel"><h2>異常告警 ({len(anomalies)} 場)</h2>{rows}</section>'


_CSS = """
* { box-sizing: border-box; }
body { font-family: 'PingFang TC','Microsoft JhengHei',-apple-system,sans-serif;
       margin: 0; background: #f6f7f9; color: #1a1a1a; }
.topnav { background: white; padding: 0 28px; border-bottom: 1px solid #e0e3e7;
          display: flex; align-items: center; }
.topnav a { display: inline-block; padding: 12px 18px; color: #555;
            text-decoration: none; font-size: 13px; font-weight: 600; }
.topnav a.active { color: #2a4d69; border-bottom: 2px solid #2a4d69; }
.topnav a:hover { color: #2a4d69; background: #f0f3f6; }
.unit-picker { margin-left: auto; padding: 8px 18px; font-size: 13px;
               color: #555; }
.unit-picker select { padding: 4px 8px; border-radius: 4px;
                       border: 1px solid #ccc; font-size: 13px;
                       margin-left: 4px; }
.period-totals { margin-top: 12px; }
.period-totals .pt-title { font-weight: 600; font-size: 14px;
                            color: #2a4d69; margin-bottom: 8px; }
.period-totals .pt-table { width: 100%; max-width: 700px;
                            border-collapse: collapse; font-size: 13px; }
.period-totals .pt-table th { background: #f0f3f6; padding: 6px 10px;
                               text-align: left; }
.period-totals .pt-table td { padding: 6px 10px; border-bottom: 1px solid #eee; }
.period-totals .pt-total { background: #e7f0f7; font-weight: 600;
                            border-top: 2px solid #2a4d69; }
.period-totals .pt-foot { font-size: 11px; color: #888; margin-top: 6px; }
header { background: linear-gradient(135deg, #2a4d69, #1a3550); color: white;
         padding: 18px 28px; }
header h1 { margin: 0 0 6px; font-size: 22px; }
header .meta { font-size: 12px; opacity: 0.92; }
header code { background: rgba(255,255,255,0.18); padding: 1px 6px;
              border-radius: 3px; font-size: 11px; }
header .badge { background: #f5b942; color: #1a3550; padding: 2px 10px;
                border-radius: 12px; font-size: 11px; font-weight: 600;
                margin-right: 8px; }
section { margin: 20px 28px; }
.kpis { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
.kpi { background: white; padding: 16px; border-radius: 6px;
       box-shadow: 0 1px 3px rgba(0,0,0,0.06); }
.kpi .label { font-size: 12px; color: #888; }
.kpi .value { font-size: 26px; font-weight: 600; margin-top: 4px; color: #2a4d69; }
.kpi .footnote { font-size: 11px; color: #999; margin-top: 4px; min-height: 14px; }
.kpi.good .value { color: #1e7c3a; }
.kpi.bad .value { color: #b03020; }
.card { background: white; padding: 20px 24px; border-radius: 6px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.06); }
.card h2 { margin: 0 0 12px; font-size: 16px; color: #2a4d69; }
.primary-section { border-left: 4px solid #2a4d69; }
.farm-picker { display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
               padding: 12px; background: #f0f3f6; border-radius: 6px;
               margin-bottom: 16px; }
.farm-picker label { font-weight: 600; }
.farm-picker select { padding: 8px 16px; border-radius: 6px;
                      border: 1px solid #ccc; font-size: 14px; min-width: 250px;
                      cursor: pointer; }
.farm-picker .data-time { margin-left: auto; font-size: 13px; color: #555; }
.meta-card { background: #fafbfc; padding: 12px 16px; border-radius: 6px;
             margin-bottom: 16px; }
.meta-row { display: flex; gap: 24px; flex-wrap: wrap; }
.meta-cell { display: flex; flex-direction: column; }
.meta-cell .lbl { font-size: 11px; color: #888; }
.meta-cell .val { font-size: 16px; font-weight: 600; color: #2a4d69; }
.chart-wrap { position: relative; height: 320px; }
.note { font-size: 12px; color: #666; margin-top: 8px; line-height: 1.5; }
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { padding: 8px 12px; text-align: right; border-bottom: 1px solid #eee; }
th { background: #f0f3f6; font-weight: 600; }
td:first-child, th:first-child { text-align: left; }
td.num { font-variant-numeric: tabular-nums; }
td.small { font-size: 12px; color: #777; }
.row-future { background: #fafbfc; }
.row-past { background: white; }
.row-total { background: #e7f0f7; font-weight: 600; border-top: 2px solid #2a4d69; }
.row-total td { padding: 10px 12px; }
.row-section td { background: #2a4d69; color: white; text-align: center;
                  font-size: 12px; font-weight: 600; padding: 6px;
                  letter-spacing: 1px; }
.clickable { cursor: pointer; }
.clickable:hover { background: #f8f9fb; }
.status { display: inline-block; padding: 2px 8px; border-radius: 10px;
          font-size: 11px; font-weight: 600; }
.status-future { background: #e8f1ff; color: #1a4480; }
.status-actual { background: #e6f4ea; color: #1e7c3a; }
.good { color: #1e7c3a; font-weight: 600; }
.warn { color: #b86700; font-weight: 600; }
.bad { color: #b03020; font-weight: 600; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 10px;
         font-size: 11px; font-weight: 600; }
.badge-ok { background: #e6f4ea; color: #1e7c3a; }
.badge-warn { background: #fff4e0; color: #a05a00; }
.badge-bad { background: #fce0db; color: #b03020; }
.alert-panel { border-left: 4px solid #b03020; }
.alert-panel.ok { border-left-color: #1e7c3a; }
.alert-row { padding: 8px 12px; margin-bottom: 6px; border-radius: 4px;
             background: #fafafa; }
.alert-row.alert { background: #fdf0ee; border-left: 3px solid #b03020; }
.alert-row.warning { background: #fff8e7; border-left: 3px solid #a05a00; }
.seg-tag { display: inline-block; padding: 1px 6px; border-radius: 3px;
           background: #e7f0f7; color: #2a4d69; font-size: 11px; font-weight: 500; }
pre { font-size: 11px; background: #f8f9fb; padding: 12px; border-radius: 4px;
      max-height: 200px; overflow-y: auto; }
"""
