# 全國牛乳產量預測儀表板 / National Milk Production Forecasting

開啟 `index.html`（同 `timeseries.html`）即可。

## 4 套儀表板

| 檔案 | 用途 |
|---|---|
| `timeseries.html` (首頁) | 主要時間序列預測、含 What-If 情境 |
| `dashboard.html` | 場別預測 + 歷史驗證 |
| `seasonal.html` | 各場各年 1-12 月乳量分布 |
| `lactation.html` | 場別/全國/個體牛泌乳曲線 |

頂部導覽列可互相切換。

## 資料來源

- DHI 月度紀錄
- 農業部〈畜牧生產〉年報
- 農業部〈在養量比較〉季報
- 農業部〈牛乳產量〉年報（僅作驗證、不入預測）

## 方法

時序模型（stl_linear / holt_winters / sarima / prophet / naive_seasonal /
neural_prophet）+ Cohort 結構模型 + Level 4 SF 涵蓋率還原校正。
詳情見儀表板「📖 方法論」章節。

最後更新：2026-05-12 00:17
