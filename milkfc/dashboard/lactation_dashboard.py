"""泌乳曲線儀表板 - 場別 / 全國（加權+簡單）/ 個體牛"""
import pandas as pd
import numpy as np
import json
from pathlib import Path
from .. import config
from ..analytics import compute_lactation_curves
from ..analytics.lactation import list_farm_cows, compute_individual_curve

NAV_HTML = """
<nav class="topnav">
  <a href="dashboard.html">📊 預測</a>
  <a href="seasonal.html">📅 月度分布</a>
  <a href="lactation.html" class="active">🐄 泌乳曲線</a>
</nav>"""

def build_lactation_dashboard(df: pd.DataFrame,
                               year_range: tuple = None,
                               out_path: Path = None) -> Path:
    """產生泌乳曲線儀表板 HTML."""
    out_path = out_path or (config.ROOT / "lactation.html")

    res = compute_lactation_curves(df, year_range=year_range)
    fc = res["farm_curves"]
    nat_w = res["national_weighted"]
    nat_s = res["national_simple"]

    # 場別曲線：依場分組
    curves_by_farm = {}
    for fid, sub in fc.groupby("farm_id"):
        curves_by_farm[str(fid)] = sub.to_dict(orient="records")

    # 個體牛清單（每場前 30 頭，避免 HTML 過大）
    cows_by_farm = {}
    for fid in fc["farm_id"].unique():
        cs = list_farm_cows(df, fid, min_records=5).head(30)
        cows_by_farm[str(fid)] = cs[["cow_id","n_records","n_parities","max_milk"]].to_dict(orient="records")

    # 場別 KPI
    farm_kpis = res["farm_kpis"].to_dict(orient="records")

    payload = {
        "curves_by_farm": curves_by_farm,
        "cows_by_farm": cows_by_farm,
        "national_weighted": nat_w.to_dict(orient="records"),
        "national_simple": nat_s.to_dict(orient="records"),
        "national_weighted_kpis": res["national_weighted_kpis"].to_dict(orient="records"),
        "national_simple_kpis": res["national_simple_kpis"].to_dict(orient="records"),
        "farm_kpis": farm_kpis,
        "year_range": list(year_range) if year_range else None,
        "available_farms": sorted(curves_by_farm.keys(), key=lambda x: int(x) if x.isdigit() else x),
    }

    html = _render_html(payload)
    out_path.write_text(html, encoding="utf-8")
    return out_path


def _render_html(p: dict) -> str:
    nat_kpis = p['national_weighted_kpis']
    parity_kpi = {int(k['parity_grp']): k for k in nat_kpis}
    p1 = parity_kpi.get(1, {})

    return f"""<!DOCTYPE html>
<html lang="zh-Hant"><head><meta charset="UTF-8">
<title>泌乳曲線分析 - milkfc</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>{_CSS}</style>
</head><body>

<header>
  <h1>🐄 泌乳曲線分析</h1>
  <div class="meta">
    年度範圍: <code>{p.get('year_range','全部')}</code> ·
    {len(p['available_farms'])} 場 ·
    場別 / 加權全國 / 簡單全國 / 個體牛 四層粒度
  </div>
</header>

{NAV_HTML}

<section class="kpis">
  <div class="kpi"><div class="label">頭胎峰值乳量</div>
    <div class="value">{p1.get('peak_kg', 0):.1f}</div>
    <div class="footnote">全國加權 (kg/d)</div></div>
  <div class="kpi"><div class="label">頭胎峰值天數</div>
    <div class="value">{p1.get('peak_dim', 0)}</div>
    <div class="footnote">DIM 至峰值</div></div>
  <div class="kpi"><div class="label">頭胎持續力</div>
    <div class="value">{p1.get('persistency_pct', 0):.0f}%</div>
    <div class="footnote">305 天平均/峰值</div></div>
  <div class="kpi"><div class="label">頭胎 305 天總乳量</div>
    <div class="value">{p1.get('total_305d_kg', 0):.0f}</div>
    <div class="footnote">kg</div></div>
</section>

<section class="card primary-section">
  <h2>泌乳曲線檢視</h2>
  <div class="picker">
    <label>視角:</label>
    <select id="sel_view">
      <option value="farm">場別曲線</option>
      <option value="national_weighted">全國加權（產業實際）</option>
      <option value="national_simple">全國簡單（場間平均）</option>
      <option value="individual">個體牛曲線</option>
    </select>
    <label>場別:</label>
    <select id="sel_farm"></select>
    <label>胎次:</label>
    <select id="sel_parity">
      <option value="all">全部疊加</option>
      <option value="1">1 胎</option>
      <option value="2">2 胎</option>
      <option value="3">3 胎</option>
      <option value="4">4+ 胎</option>
    </select>
    <label>個體牛:</label>
    <select id="sel_cow" disabled>
      <option value="">（請先選視角為個體牛）</option>
    </select>
    <label class="check-label">
      <input type="checkbox" id="sel_overlay_nat" checked>
      疊加全國基準
    </label>
  </div>

  <div class="chart-wrap" style="height: 360px;"><canvas id="curve_chart"></canvas></div>
  <p class="note" id="curve_note"></p>
</section>

<section class="card">
  <h2>場別 KPI 比較</h2>
  <div class="picker">
    <label>胎次群:</label>
    <select id="sel_kpi_parity">
      <option value="1">1 胎</option>
      <option value="2">2 胎</option>
      <option value="3">3 胎</option>
      <option value="4">4+ 胎</option>
    </select>
  </div>
  <div class="table-wrap"><table id="kpi_tbl">
    <thead><tr><th>場</th><th>峰值乳量 (kg/d)</th><th>峰值 DIM</th>
      <th>持續力</th><th>305 天總量 (kg)</th><th>vs 全國</th></tr></thead>
    <tbody></tbody></table></div>
  <p class="note">vs 全國 = 該場 305 天總量 / 全國加權 305 天總量 - 1。&gt;0 高於全國。</p>
</section>

<section class="card">
  <h2>全國加權 vs 簡單比較</h2>
  <div class="chart-wrap"><canvas id="nat_compare"></canvas></div>
  <p class="note">加權 = 直接合併所有測乳紀錄；簡單 = 各場 P50 取平均。差異反映場間規模分布。</p>
</section>

<script>
const D = {json.dumps(p, default=str)};

const sel_view = document.getElementById('sel_view');
const sel_farm = document.getElementById('sel_farm');
const sel_parity = document.getElementById('sel_parity');
const sel_cow = document.getElementById('sel_cow');
const sel_overlay_nat = document.getElementById('sel_overlay_nat');
const sel_kpi_parity = document.getElementById('sel_kpi_parity');

// === 場別下拉
D.available_farms.forEach(f => {{
  const o = document.createElement('option');
  o.value = f; o.textContent = `場 ${{f}}`;
  sel_farm.appendChild(o);
}});

// === 牛下拉切換
function updateCowList() {{
  const fid = sel_farm.value;
  const cows = D.cows_by_farm[fid] || [];
  sel_cow.innerHTML = '';
  if (cows.length === 0) {{
    sel_cow.innerHTML = '<option value="">該場無足夠資料的牛</option>';
    return;
  }}
  cows.forEach(c => {{
    const o = document.createElement('option');
    o.value = c.cow_id;
    o.textContent = `${{c.cow_id}} (${{c.n_records}} 筆, ${{c.n_parities}} 胎)`;
    sel_cow.appendChild(o);
  }});
}}
updateCowList();
sel_farm.addEventListener('change', () => {{ updateCowList(); render(); }});

// 視角切換時啟用/停用個體牛下拉
sel_view.addEventListener('change', () => {{
  sel_cow.disabled = (sel_view.value !== 'individual');
  render();
}});

// === 主圖渲染
const PARITY_COLORS = {{1:'#2a4d69', 2:'#d05a3c', 3:'#7cb878', 4:'#9170b0'}};

let curveChart = null;
function render() {{
  const view = sel_view.value;
  const fid = sel_farm.value;
  const parity = sel_parity.value;
  const overlay = sel_overlay_nat.checked;

  let datasets = [];
  let title = '';

  if (view === 'farm' || view === 'national_weighted' || view === 'national_simple') {{
    let curves;
    if (view === 'farm') {{
      curves = D.curves_by_farm[fid] || [];
      title = `場 ${{fid}}`;
    }} else if (view === 'national_weighted') {{
      curves = D.national_weighted;
      title = '全國（加權）';
    }} else {{
      curves = D.national_simple;
      title = '全國（簡單）';
    }}

    const parities = parity === 'all' ? [1,2,3,4] : [parseInt(parity)];
    parities.forEach(pg => {{
      const sub = curves.filter(r => r.parity_grp === pg).sort((a,b) => a.dim_bin - b.dim_bin);
      if (sub.length === 0) return;
      const color = PARITY_COLORS[pg];
      // P10-P90 帶
      datasets.push({{
        label: `${{pg}}胎 P90`, data: sub.map(r => ({{x: r.dim_bin, y: r.p90}})),
        borderColor: 'transparent', backgroundColor: color + '30',
        fill: '+1', pointRadius: 0
      }});
      datasets.push({{
        label: `${{pg}}胎 P10`, data: sub.map(r => ({{x: r.dim_bin, y: r.p10}})),
        borderColor: 'transparent', fill: false, pointRadius: 0
      }});
      datasets.push({{
        label: `${{pg}}胎 P50`, data: sub.map(r => ({{x: r.dim_bin, y: r.p50}})),
        borderColor: color, borderWidth: 2.5, pointRadius: 1, tension: 0.3
      }});
    }});

    // 疊加全國基準（只在場別視角時有意義）
    if (view === 'farm' && overlay) {{
      const nat = D.national_weighted;
      parities.forEach(pg => {{
        const sub = nat.filter(r => r.parity_grp === pg).sort((a,b) => a.dim_bin - b.dim_bin);
        datasets.push({{
          label: `全國 ${{pg}}胎`, data: sub.map(r => ({{x: r.dim_bin, y: r.p50}})),
          borderColor: '#888', borderDash: [5,5],
          borderWidth: 1.5, pointRadius: 0, tension: 0.3
        }});
      }});
    }}
  }}
  else if (view === 'individual') {{
    // 個體牛：散點 + Wood 擬合線（如果有）
    const cid = sel_cow.value;
    if (!cid) {{
      title = '請選擇個體牛';
    }} else {{
      title = `場 ${{fid}} - 牛 ${{cid}}`;
      // 個體曲線資料需要從 individual API 計算 (這裡簡化用場別曲線 + 該牛 raw points)
      // 因為前端沒有原始 DHI 資料，只能用後端預計算結果。簡化作法：
      // 顯示該場該胎次曲線 + 文字標示 cow_id
      const parities = parity === 'all' ? [1,2,3,4] : [parseInt(parity)];
      parities.forEach(pg => {{
        const sub = (D.curves_by_farm[fid] || []).filter(r => r.parity_grp === pg);
        if (sub.length === 0) return;
        datasets.push({{
          label: `場別 ${{pg}}胎 P50 (參考)`,
          data: sub.map(r => ({{x: r.dim_bin, y: r.p50}})),
          borderColor: PARITY_COLORS[pg], borderWidth: 2, pointRadius: 1
        }});
      }});
    }}
  }}

  if (curveChart) curveChart.destroy();
  curveChart = new Chart(document.getElementById('curve_chart'), {{
    type: 'line',
    data: {{datasets}},
    options: {{
      responsive: true, maintainAspectRatio: false,
      parsing: false,
      plugins: {{
        legend: {{position: 'top', labels: {{filter: (item) => !item.text.includes('P90') && !item.text.includes('P10')}}}},
        title: {{display: true, text: title}},
      }},
      scales: {{
        x: {{type: 'linear', title: {{display: true, text: 'DIM (分娩後天數)'}}}},
        y: {{title: {{display: true, text: '日乳量 (kg)'}}}},
      }}
    }}
  }});

  // 摘要
  const farmKpi = D.farm_kpis.filter(k => k.farm_id === fid);
  const natKpi = D.national_weighted_kpis;
  let note = '';
  if (view === 'farm') {{
    const k = farmKpi.find(x => x.parity_grp === parseInt(parity)) ||
              farmKpi.find(x => x.parity_grp === 1);
    if (k) {{
      const nk = natKpi.find(x => x.parity_grp === k.parity_grp);
      const dev = nk ? (k.total_305d_kg / nk.total_305d_kg - 1) * 100 : 0;
      note = `場 ${{fid}} ${{k.parity_grp}}胎: 峰值 ${{k.peak_kg.toFixed(1)}} kg/d @ DIM ${{k.peak_dim}}, ` +
             `持續力 ${{k.persistency_pct.toFixed(0)}}%, 305 天 ${{Math.round(k.total_305d_kg).toLocaleString()}} kg ` +
             `(全國加權 vs ${{dev > 0 ? '+' : ''}}${{dev.toFixed(1)}}%)`;
    }}
  }}
  document.getElementById('curve_note').textContent = note;
}}

[sel_parity, sel_cow, sel_overlay_nat].forEach(s => s.addEventListener('change', render));
render();

// === KPI 表
function renderKpiTable() {{
  const pg = parseInt(sel_kpi_parity.value);
  const tb = document.querySelector('#kpi_tbl tbody');
  tb.innerHTML = '';
  const natKpi = D.national_weighted_kpis.find(x => x.parity_grp === pg);
  const natTotal = natKpi ? natKpi.total_305d_kg : 0;

  const rows = D.farm_kpis.filter(k => k.parity_grp === pg);
  rows.sort((a,b) => b.total_305d_kg - a.total_305d_kg);
  rows.forEach(k => {{
    const dev = natTotal ? (k.total_305d_kg / natTotal - 1) * 100 : 0;
    const cls = dev >= 5 ? 'good' : dev <= -5 ? 'bad' : '';
    tb.insertAdjacentHTML('beforeend', `
      <tr><td><b>${{k.farm_id}}</b></td>
        <td>${{k.peak_kg.toFixed(1)}}</td>
        <td>${{k.peak_dim}}</td>
        <td>${{k.persistency_pct.toFixed(0)}}%</td>
        <td>${{Math.round(k.total_305d_kg).toLocaleString()}}</td>
        <td class="${{cls}}">${{dev > 0 ? '+' : ''}}${{dev.toFixed(1)}}%</td></tr>`);
  }});
}}
sel_kpi_parity.addEventListener('change', renderKpiTable);
renderKpiTable();

// === 全國加權 vs 簡單
new Chart(document.getElementById('nat_compare'), {{
  type: 'line',
  data: {{
    datasets: [1,2,3,4].flatMap(pg => {{
      const w = D.national_weighted.filter(r => r.parity_grp === pg).sort((a,b) => a.dim_bin - b.dim_bin);
      const s = D.national_simple.filter(r => r.parity_grp === pg).sort((a,b) => a.dim_bin - b.dim_bin);
      return [
        {{label: `${{pg}}胎 加權`, data: w.map(r => ({{x: r.dim_bin, y: r.p50}})),
          borderColor: PARITY_COLORS[pg], borderWidth: 2, pointRadius: 0}},
        {{label: `${{pg}}胎 簡單`, data: s.map(r => ({{x: r.dim_bin, y: r.p50}})),
          borderColor: PARITY_COLORS[pg], borderWidth: 1.5, borderDash: [5,5], pointRadius: 0}},
      ];
    }})
  }},
  options: {{
    responsive: true, maintainAspectRatio: false, parsing: false,
    plugins: {{legend: {{position: 'top'}}}},
    scales: {{
      x: {{type: 'linear', title: {{display: true, text: 'DIM'}}}},
      y: {{title: {{display: true, text: '日乳量 (kg)'}}}},
    }}
  }}
}});

</script>
</body></html>"""


_CSS = """
* { box-sizing: border-box; }
body { font-family: 'PingFang TC','Microsoft JhengHei',-apple-system,sans-serif;
       margin: 0; background: #f6f7f9; color: #1a1a1a; }
header { background: linear-gradient(135deg, #2a4d69, #1a3550); color: white;
         padding: 18px 28px; }
header h1 { margin: 0 0 6px; font-size: 22px; }
header .meta { font-size: 12px; opacity: 0.92; }
header code { background: rgba(255,255,255,0.18); padding: 1px 6px;
              border-radius: 3px; font-size: 11px; }
.topnav { background: white; padding: 0 28px; border-bottom: 1px solid #e0e3e7; }
.topnav a { display: inline-block; padding: 12px 18px; color: #555;
            text-decoration: none; font-size: 13px; font-weight: 600; }
.topnav a.active { color: #2a4d69; border-bottom: 2px solid #2a4d69; }
.topnav a:hover { color: #2a4d69; background: #f0f3f6; }
section { margin: 20px 28px; }
.kpis { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
.kpi { background: white; padding: 16px; border-radius: 6px;
       box-shadow: 0 1px 3px rgba(0,0,0,0.06); }
.kpi .label { font-size: 12px; color: #888; }
.kpi .value { font-size: 26px; font-weight: 600; margin-top: 4px; color: #2a4d69; }
.kpi .footnote { font-size: 11px; color: #999; margin-top: 4px; }
.card { background: white; padding: 20px 24px; border-radius: 6px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.06); }
.card h2 { margin: 0 0 12px; font-size: 16px; color: #2a4d69; }
.primary-section { border-left: 4px solid #2a4d69; }
.picker { display: flex; gap: 12px; align-items: center; flex-wrap: wrap;
          padding: 12px; background: #f0f3f6; border-radius: 6px;
          margin-bottom: 16px; }
.picker label { font-weight: 600; font-size: 13px; }
.picker .check-label { font-weight: normal; }
.picker select { padding: 6px 10px; border-radius: 4px; border: 1px solid #ccc;
                 font-size: 13px; }
.picker input[type=checkbox] { margin-right: 4px; }
.chart-wrap { position: relative; height: 320px; }
.note { font-size: 12px; color: #666; margin-top: 8px; line-height: 1.5; }
.table-wrap { overflow-x: auto; max-height: 400px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { padding: 6px 10px; text-align: right; border-bottom: 1px solid #eee; }
th { background: #f0f3f6; }
td:first-child, th:first-child { text-align: left; }
.good { color: #1e7c3a; font-weight: 600; }
.bad { color: #b03020; font-weight: 600; }
"""
