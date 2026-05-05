"""月度分布儀表板 - 場別 vs 全國（加權/簡單）季節型態"""
import pandas as pd
import numpy as np
import json
from pathlib import Path
from .. import config
from ..analytics import compute_monthly_distribution, compute_national_monthly
from ..analytics.seasonal import summary_stats

NAV_HTML = """
<nav class="topnav">
  <a href="dashboard.html">📊 預測</a>
  <a href="seasonal.html" class="active">📅 月度分布</a>
  <a href="lactation.html">🐄 泌乳曲線</a>
</nav>"""

def build_seasonal_dashboard(df: pd.DataFrame,
                              year_range: tuple = None,
                              out_path: Path = None) -> Path:
    """產生月度分布儀表板 HTML."""
    out_path = out_path or (config.ROOT / "seasonal.html")

    farm_monthly = compute_monthly_distribution(df, year_range=year_range)
    nat_w = compute_national_monthly(farm_monthly, weighted=True)
    nat_s = compute_national_monthly(farm_monthly, weighted=False)

    # 摘要統計
    nat_w_stats = summary_stats(nat_w)
    nat_s_stats = summary_stats(nat_s)

    # 整理場別資料：依場分組轉成 dict
    fm_by_farm = {}
    farm_summary = []
    for fid, sub in farm_monthly.groupby("farm_id"):
        fm_by_farm[str(fid)] = sub.to_dict(orient="records")
        stats = summary_stats(sub)
        farm_summary.append({
            "farm_id": str(fid),
            "n_records": int(len(sub)),
            "avg_monthly_milk": float(sub["total_milk_kg"].mean()),
            **stats
        })

    payload = {
        "farm_monthly_by_farm": fm_by_farm,
        "national_weighted": nat_w.to_dict(orient="records"),
        "national_simple": nat_s.to_dict(orient="records"),
        "national_weighted_stats": nat_w_stats,
        "national_simple_stats": nat_s_stats,
        "farm_summary": farm_summary,
        "year_range": list(year_range) if year_range else None,
        "available_years": sorted(farm_monthly["year"].unique().tolist()),
    }

    html = _render_html(payload)
    out_path.write_text(html, encoding="utf-8")
    return out_path


def _render_html(p: dict) -> str:
    return f"""<!DOCTYPE html>
<html lang="zh-Hant"><head><meta charset="UTF-8">
<title>月度乳量分布 - milkfc</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>{_CSS}</style>
</head><body>

<header>
  <h1>📅 月度乳量分布分析</h1>
  <div class="meta">
    年度範圍: <code>{p.get('year_range','全部')}</code> ·
    {len(p['farm_summary'])} 場 ·
    支援加權/簡單兩種全國基準
  </div>
</header>

{NAV_HTML}

<section class="kpis">
  <div class="kpi"><div class="label">全國（加權）旺月</div>
    <div class="value">{p['national_weighted_stats'].get('peak_month','—')} 月</div>
    <div class="footnote">乳量最高的月份</div></div>
  <div class="kpi"><div class="label">全國（加權）淡月</div>
    <div class="value">{p['national_weighted_stats'].get('trough_month','—')} 月</div>
    <div class="footnote">乳量最低的月份</div></div>
  <div class="kpi"><div class="label">夏季衰退率</div>
    <div class="value">{p['national_weighted_stats'].get('summer_drop_pct',0):.1f}%</div>
    <div class="footnote">峰月→淡月的下降幅度</div></div>
  <div class="kpi"><div class="label">月度變異 CV</div>
    <div class="value">{p['national_weighted_stats'].get('cv_pct',0):.1f}%</div>
    <div class="footnote">12 個月相對波動</div></div>
</section>

<section class="card primary-section">
  <h2>單場月度分布</h2>
  <div class="picker">
    <label>場別:</label>
    <select id="sel_farm"></select>
    <label>顯示模式:</label>
    <select id="sel_mode">
      <option value="absolute">絕對乳量 (kg)</option>
      <option value="season_index">季節指數 (相對年均)</option>
    </select>
    <label>全國基準:</label>
    <select id="sel_scope">
      <option value="weighted">加權（產業實際）</option>
      <option value="simple">簡單（場間平均）</option>
      <option value="none">不顯示</option>
    </select>
  </div>

  <div class="chart-wrap"><canvas id="chart_farm_year"></canvas></div>
  <p class="note" id="farm_note"></p>

  <h3 style="margin-top: 20px;">該場跨年度月分布熱圖</h3>
  <div class="table-wrap"><table id="heat_tbl">
    <thead><tr><th>年度</th><th>1月</th><th>2月</th><th>3月</th><th>4月</th>
      <th>5月</th><th>6月</th><th>7月</th><th>8月</th>
      <th>9月</th><th>10月</th><th>11月</th><th>12月</th></tr></thead>
    <tbody></tbody>
  </table></div>
  <p class="note">紅 = 該年該月 &gt; 該場該年平均，藍 = 反之。顏色越深差距越大。</p>
</section>

<section class="card">
  <h2>全國月度分布（加權 vs 簡單）</h2>
  <div class="chart-wrap"><canvas id="chart_national"></canvas></div>
  <p class="note">
    <b>加權</b> = 直接加總全部場（保留「大場通常產量也高」的相關性，產業實際總量）。<br>
    <b>簡單</b> = 平均每頭日乳量 × 平均場頭數 × 場數（拆掉相關性，反映「如果每場都是典型大小」的估算）。<br>
    兩條線差距越大 = 場間「規模 × 產量」的相關性越強。在台灣通常加權 &gt; 簡單，
    因為大場通常每頭產量也高（飼養管理較專業）。
  </p>
</section>

<section class="card">
  <h2>場別摘要（按月度變異排序）</h2>
  <div class="table-wrap"><table id="farm_tbl">
    <thead><tr><th>場</th><th>月平均乳量</th><th>旺月</th><th>淡月</th>
      <th>夏季衰退</th><th>變異 CV</th></tr></thead>
    <tbody></tbody></table></div>
</section>

<script>
const D = {json.dumps(p, default=str)};

const sel_farm = document.getElementById('sel_farm');
const sel_mode = document.getElementById('sel_mode');
const sel_scope = document.getElementById('sel_scope');

// === 場別下拉 ===
D.farm_summary.sort((a,b) => b.avg_monthly_milk - a.avg_monthly_milk);
D.farm_summary.forEach(f => {{
  const o = document.createElement('option');
  o.value = f.farm_id;
  o.textContent = `場 ${{f.farm_id}} (年均 ${{Math.round(f.avg_monthly_milk/1000).toLocaleString()}} 噸/月)`;
  sel_farm.appendChild(o);
}});

let farmYearChart = null;
let nationalChart = null;

const COLORS = ['#2a4d69','#d05a3c','#7cb878','#e2a85a','#9170b0','#5a9eb0','#c2756a'];

function renderFarmYear() {{
  const fid = sel_farm.value;
  const mode = sel_mode.value;
  const scope = sel_scope.value;

  const data = D.farm_monthly_by_farm[fid] || [];
  const yearsAvail = [...new Set(data.map(r => r.year))].sort();
  const months = Array.from({{length:12}}, (_,i) => i+1);

  // 每個年度一條線
  const datasets = yearsAvail.map((y, idx) => {{
    const yearData = months.map(m => {{
      const row = data.find(r => r.year === y && r.month === m);
      if (!row) return null;
      return mode === 'absolute' ? row.total_milk_kg : row.season_index;
    }});
    return {{
      label: `${{y}}`, data: yearData,
      borderColor: COLORS[idx % COLORS.length],
      backgroundColor: COLORS[idx % COLORS.length],
      borderWidth: 2, pointRadius: 3, tension: 0.3,
    }};
  }});

  // 全國基準線
  if (scope !== 'none') {{
    const natData = scope === 'weighted' ? D.national_weighted : D.national_simple;
    const natByYear = {{}};
    natData.forEach(r => {{
      if (!natByYear[r.year]) natByYear[r.year] = {{}};
      natByYear[r.year][r.month] = mode === 'absolute' ? r.avg_per_farm : r.season_index;
    }});
    // 取最近年份
    const latest = yearsAvail[yearsAvail.length - 1];
    const natRow = months.map(m => natByYear[latest] ? natByYear[latest][m] : null);
    datasets.push({{
      label: `全國基準 ${{latest}} (${{scope === 'weighted' ? '加權' : '簡單'}})`,
      data: natRow, borderColor: '#666', backgroundColor: '#666',
      borderWidth: 2, pointRadius: 2, borderDash: [5,5], tension: 0.3,
    }});
  }}

  if (farmYearChart) farmYearChart.destroy();
  farmYearChart = new Chart(document.getElementById('chart_farm_year'), {{
    type: 'line',
    data: {{labels: months.map(m => `${{m}}月`), datasets}},
    options: {{responsive: true, maintainAspectRatio: false,
      plugins: {{legend: {{position:'top'}}}},
      scales: {{y: {{title: {{display: true,
        text: mode === 'absolute' ? '月乳量 (kg)' : '季節指數 (=1.0 為年均)'}}}}}}
    }}
  }});

  // 摘要
  const f = D.farm_summary.find(x => x.farm_id === fid);
  if (f) {{
    document.getElementById('farm_note').textContent =
      `場 ${{fid}} | 月平均 ${{Math.round(f.avg_monthly_milk/1000).toLocaleString()}} 噸 ` +
      `| 旺月 ${{f.peak_month}}月 | 淡月 ${{f.trough_month}}月 ` +
      `| 夏季衰退 ${{f.summer_drop_pct?.toFixed(1) || '-'}}% | CV ${{f.cv_pct?.toFixed(1) || '-'}}%`;
  }}

  // 熱圖
  const heat_tb = document.querySelector('#heat_tbl tbody');
  heat_tb.innerHTML = '';
  yearsAvail.forEach(y => {{
    const yearData = data.filter(r => r.year === y);
    const yearAvg = yearData.reduce((s,r) => s + r.total_milk_kg, 0) / yearData.length;
    let row = `<tr><td><b>${{y}}</b></td>`;
    months.forEach(m => {{
      const r = yearData.find(x => x.month === m);
      if (!r) {{ row += '<td>—</td>'; return; }}
      const dev = (r.total_milk_kg / yearAvg - 1) * 100;
      const intensity = Math.min(Math.abs(dev) / 20, 1);
      const color = dev >= 0
        ? `rgba(180,80,60,${{intensity}})`
        : `rgba(60,100,180,${{intensity}})`;
      const txtColor = intensity > 0.5 ? 'white' : 'inherit';
      row += `<td style="background:${{color}};color:${{txtColor}}" title="${{Math.round(r.total_milk_kg).toLocaleString()}} kg">${{dev>=0?'+':''}}${{dev.toFixed(0)}}%</td>`;
    }});
    row += '</tr>';
    heat_tb.insertAdjacentHTML('beforeend', row);
  }});
}}

[sel_farm, sel_mode, sel_scope].forEach(s => s.addEventListener('change', renderFarmYear));
renderFarmYear();

// === 全國加權 vs 簡單 ===
function natChart() {{
  const yearsAvail = [...new Set(D.national_weighted.map(r => r.year))].sort();
  const months = Array.from({{length:12}}, (_,i) => i+1);
  const labels = [];
  const wData = [];
  const sData = [];
  yearsAvail.forEach(y => months.forEach(m => {{
    labels.push(`${{y}}-${{String(m).padStart(2,'0')}}`);
    const w = D.national_weighted.find(r => r.year === y && r.month === m);
    const s = D.national_simple.find(r => r.year === y && r.month === m);
    wData.push(w ? w.total_milk_kg : null);
    sData.push(s ? s.total_milk_kg : null);
  }}));
  if (nationalChart) nationalChart.destroy();
  nationalChart = new Chart(document.getElementById('chart_national'), {{
    type: 'line',
    data: {{labels, datasets: [
      {{label: '加權（產業實際）', data: wData, borderColor: '#2a4d69',
        borderWidth: 2.5, pointRadius: 1, tension: 0.3}},
      {{label: '簡單（典型場×場數）', data: sData, borderColor: '#d05a3c',
        borderWidth: 2, pointRadius: 1, borderDash: [4,4], tension: 0.3}}
    ]}},
    options: {{responsive: true, maintainAspectRatio: false,
      plugins: {{legend: {{position:'top'}}}},
      scales: {{y: {{title: {{display: true, text: '加總月乳量 (kg)'}}}}}}
    }}
  }});
}}
natChart();

// === 場別摘要表 ===
const tb = document.querySelector('#farm_tbl tbody');
[...D.farm_summary].sort((a,b) => (b.cv_pct||0) - (a.cv_pct||0)).forEach(f => {{
  tb.insertAdjacentHTML('beforeend', `
    <tr><td><b>${{f.farm_id}}</b></td>
      <td>${{Math.round(f.avg_monthly_milk/1000).toLocaleString()}} 噸</td>
      <td>${{f.peak_month || '-'}} 月</td>
      <td>${{f.trough_month || '-'}} 月</td>
      <td>${{f.summer_drop_pct?.toFixed(1) || '-'}}%</td>
      <td>${{f.cv_pct?.toFixed(1) || '-'}}%</td></tr>`);
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
.card h3 { font-size: 14px; color: #2a4d69; margin: 0 0 8px; }
.primary-section { border-left: 4px solid #2a4d69; }
.picker { display: flex; gap: 12px; align-items: center; flex-wrap: wrap;
          padding: 12px; background: #f0f3f6; border-radius: 6px;
          margin-bottom: 16px; }
.picker label { font-weight: 600; font-size: 13px; }
.picker select { padding: 6px 10px; border-radius: 4px; border: 1px solid #ccc;
                 font-size: 13px; }
.chart-wrap { position: relative; height: 320px; }
.note { font-size: 12px; color: #666; margin-top: 8px; line-height: 1.5; }
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { padding: 6px 10px; text-align: right; border-bottom: 1px solid #eee; }
th { background: #f0f3f6; }
td:first-child, th:first-child { text-align: left; }
"""
