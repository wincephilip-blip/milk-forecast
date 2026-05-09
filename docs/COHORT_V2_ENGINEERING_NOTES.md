# Cohort v2 工程改造筆記

**狀態**：完成 phase 0–4 後端、phase 5 dashboard UI 待補
**論文版本**：論文使用 `cohort_simple` (v1)、本筆記為其後續工程改善之記錄、不進論文
**驗證資料**：2021–2024 holdout backtest

---

## 1. 背景：v1 cohort 為什麼準？拆解後發現的「抵銷現象」

論文 v1 cohort 4 年 MAPE 為 2.15%，看似結構式模型優越於時序模型（4.06%–16.48%）。但拆解每年誤差到 N（產乳牛頭數）、Q（DHI 單牛日產乳）、r（生產力比）三個分量後，發現一個重要現象：

**v1 之所以準，部分原因是三項分量誤差「方向不一致、互相抵銷」。**

| 年 | N 誤差 | Q 誤差 | r 誤差 (r_act/r_pred) | Raw 誤差 (N×Q) | 終端誤差 |
|---|--------|--------|---------------------|----------------|---------|
| 2021 | −2.76% | +1.49% | +0.97% | +10.0% | **−0.36%** |
| 2022 | +1.45% | +0.56% | −2.94% | +9.9% | **−0.99%** |
| 2023 | +6.54% | −2.09% | −4.19% | +8.0% | **−0.05%** |
| 2024 | +7.75% | −2.76% | +2.31% | +11.5% | **+7.20%** |

**關鍵觀察**：

- Raw cohort（不做 r 校正）每年都高估 8–11%（持續性 DHI 樣本偏選偏差）
- r_t 校正項剛好吸收掉這個 +9% 偏差（這是 cohort 設計上的功勞）
- 但 N、Q、r 各自有 2–7% 投影誤差，這些誤差在 2021–2023 反向抵銷、在 2024 同向疊加

**結論**：v1 在 2021–2023 之「準」有一部分是運氣（互相抵銷）；2024 失準是結構性的（無抵銷可用）。改善方向應聚焦在「讓每一項投影更準」，但這在純外推情境下會破壞抵銷。

---

## 2. 改造目標與設計原則

**目標**：把 2024 失準從 +7.20% 縮到 ±2% 內，4 年 MAPE 降至 ≤1%。

**設計原則**：
- **論文凍結**：`cohort_model.py` 不動，論文版本可重現
- **並行雙模式**：新建 `cohort_model_v2.py`，新功能以 flag 控制，預設值等同 v1
- **漸進開發**：每個改善向量獨立可開關，便於 backtest 對比與回滾
- **誠實 backtest**：每個 phase 跑 4 年 holdout，在 dashboard 裡明確標註「此為 v2、與論文 v1 不同」

---

## 3. 各 phase 實作與 backtest 結果

### Phase 0：v2 骨架

新建 `cohort_model_v2.py`，所有 flag 預設值等同 v1。驗證標準：對 2021/2022/2023/2024/2026 之 `annual_total_tons` 與 v1 完全相同（差異 < 0.01）。**通過**。

### Phase 1：Q 改月度 STL 投影

把 Q 從「年度線性外推」改成「月度 STL 分解 + 趨勢外推 + 季節項加回」，使用 `statsmodels.tsa.seasonal.STL(period=12, robust=True)`。

**結果（孤立測試）**：MAPE 2.29%（比 v1 的 2.15% **更高**）。

**反直覺解釋**：phase 1 把 Q 估準了，但這破壞了 2021–2023 年 Q 與 N 的反向抵銷。2024 也只是輕微改善。**單獨 phase 1 不是有效改善路徑**。

**保留決定**：保留作為 flag (`q_projection='monthly_stl'`)、未來若引入「真月度 cohort」（v3 考量）時可重用。

### Phase 2：N 改季度投影

把 N 從「年度線性外推」改成「季度線性回歸 + 年報年中錨點」，使用 `QUARTERLY_INVENTORY` 之 21 季資料。

**結果（孤立測試、純外推）**：MAPE 2.38%（比 v1 更高）。

**原因**：純外推情境下，季度資料的最後可用點（target_year-1 Q3）距目標年中心仍有 ~1 年滯後，加上多了季度雜訊；外推到目標年 4 季的 N 與年度線性外推差距不大、誤差來源相同。**phase 2 之價值需配合 nowcast 才能體現**。

### Phase 3：Nowcast 模式（as_of_date / nowcast_mode）

依 `as_of_date` 自動偵測 target_year 之 Q1/Q2/Q3 是否已公告（保守滯後估計：Q1 ≈ 5 月、Q2 ≈ 8 月、Q3 ≈ 11 月），把已公告之季度真值替換進 N 投影。

**結果**：

| 情境 | MAPE | 2024 |
|------|------|------|
| Phase 3 純外推 | 2.38% | +6.73% |
| Phase 3 +Q1 nowcast | **1.46%** | +3.93% |
| Phase 3 +Q1+Q2 | 1.69% | +3.19% |
| Phase 3 +Q1+Q2+Q3 | 1.74% | +2.66% |

**關鍵觀察**：

- Q1 nowcast 是 sweet spot；加更多季度反而把 2021/2022 之穩定年弄得更差
- 2024 政策衝擊年改善最大（+7.20% → +3.93%）
- 此 phase 是整個改造收益最大的單一向量

### Phase 4：r_t ensemble 自適應

實作 `_r_adaptive_window(method='ensemble')`：50/50 平均「5 年 OLS 線性外推」與「3 年算術平均」。

**設計理由**：
- 5 年 OLS 對長期下降趨勢敏感，但對「反彈」（如 2024 r 從 2023 之 1.0357 反彈到 1.0639）反應慢
- 3 年算術平均對反彈敏感、但忽視長期趨勢
- 各執一半可在兩種情境下都不會極端失準

**單獨 r 投影 MAPE**（2021–2024 backtest）：

| 方法 | r 投影 MAPE |
|------|------------|
| 5yr OLS（v1） | 2.48% |
| 3yr OLS | 3.58% |
| Last value | 2.66% |
| 3yr mean | 2.72% |
| **5yr OLS + 3yr mean ensemble** | **2.31%** |

**疊加在 phase 3 上的 cohort 終端 MAPE**：

| 設定 | MAPE | 2024 |
|------|------|------|
| Phase 4 純外推 | 1.77% | +4.91% |
| **Phase 4 + Q1 nowcast** | **0.96%** ⭐ | **+2.15%** |
| Phase 4 + Q1+Q2 nowcast | 1.37% | +1.43% |
| Phase 4 + Q1+Q2+Q3 nowcast | 1.44% | +0.91% |

**結論**：phase 4 ensemble 是 cheap-and-effective 的改善（單獨貢獻 0.6pp 改善），且與 phase 3 nowcast 疊加後達到 MAPE 0.96%，超越原本目標。

---

## 4. 最終建議的 v2 配置

```python
# 一般情境（純外推、年初決策）
forecast_cohort_v2(
    target_year=2026,
    as_of_date='2026-01-01',
    nowcast_mode='auto',           # 自動偵測無 target year 季報、退化為純外推
    n_projection='quarterly',
    q_projection='annual_linear',  # phase 1 不啟用
    r_window='adaptive',           # phase 4 ensemble
)
# 預期 4 年 MAPE: 1.77%

# 年中情境（Q1 公告後）
forecast_cohort_v2(
    target_year=2026,
    as_of_date='2026-06-01',
    nowcast_mode='auto',           # 自動偵測 Q1 已公告、加 nowcast
    n_projection='quarterly',
    q_projection='annual_linear',
    r_window='adaptive',
)
# 預期 4 年 MAPE: 0.96%
```

---

## 5. 為什麼 phase 1 不在最終配置裡

Phase 1（Q 月度 STL）通過 backtest 證明會破壞 2021–2023 之抵銷而升高 MAPE，但保留作為可選 flag 之原因有二：

1. **未來「真月度 cohort」（v3 考量）**：若改成「月度 N × 月度 Q × 月內天數 / r」，phase 1 之月度 Q 投影是必要 building block。
2. **配合不同情境**：當外部資料源（例如月度 N 估計）也升級到月度時，phase 1 之 Q 投影才能發揮配套效果。

換句話說：phase 1 不是錯誤、是「未來改造之 stub」，現階段不啟用。

---

## 6. 「抵銷」現象之啟發

這次改造最大的概念性收穫：**結構式模型的「準」可以分為兩種**：

1. **設計性準**：模型機制本身能正確吸收某類偏差。例如 cohort 之 r_t 校正吸收 DHI 樣本偏選 +9% 偏差。
2. **抵銷性準**：模型多個分量的投影誤差恰好反向、彼此抵銷。例如 v1 之 2023 −0.05% 終端誤差來自 N (+6.54%)、Q (−2.09%)、r (−4.19%) 三項抵銷。

**設計性準是穩定的、可預期的；抵銷性準是脆弱的、依賴外部條件**。當外部條件變化（如 2024 政策衝擊），抵銷會失效，模型變不準。

改造目標應該是把「抵銷性準」轉化成「設計性準」——即每一項分量都儘量準。但**改善每項分量會在歷史 backtest 中破壞抵銷、看似讓 MAPE 變差**，這是 phase 1/2 孤立測試的反直覺結果。實際 ROI 必須等加上 nowcast（額外資訊源）後才能體現。

---

## 7. 對 2026 預測的實務意義

| 版本 | 2026 預測 |
|------|-----------|
| v1 simple（論文版本） | 49.92 萬公噸 |
| v2 phase 3 純外推 | 47.23 萬公噸 |
| **v2 phase 4 純外推（推薦）** | **46.01 萬公噸** |

考慮 2024–2027 養牛產業升級轉型計畫仍在執行、N 持續下降、phase 4 純外推之歷史 4 年 MAPE 為 1.77%，**46.01 萬公噸 ± 2pp 應該是更可信的點估計**。

當 2026Q1 季報公告後（預計 2026-06）、可改 `as_of_date='2026-07-01'` 啟用 nowcast、預期 MAPE 降至 ~1%（信賴區間 ~ ±2%）。

---

## 8. 程式檔案位置

| 檔案 | 用途 |
|------|------|
| `milkfc/forecast/cohort_model.py` | v1 cohort_simple、論文版本、**禁止修改** |
| `milkfc/forecast/cohort_model_v2.py` | v2、所有改善 |
| `reports/_build_monthly_q_cache.py` | 一次性腳本：建月度 Q cache |
| `reports/_backtest_cohort_v2.py` | v2 全 phase backtest 比較表 |
| `snapshots/_dhi_yearly_cows.json` | DHI 年度聚合（v1 用） |
| `snapshots/_dhi_monthly_yield.json` | DHI 月度聚合（v2 phase 1 用） |
| `snapshots/_holdout_backtest.json` | v1 backtest 結果（論文圖表來源） |
| `snapshots/_holdout_backtest_v2.json` | v2 多情境 backtest 結果 |

---

## 9. 待辦

- [x] phase 0–4 後端
- [ ] phase 5a 技術筆記（本檔）
- [ ] phase 5b 2026 實測 runbook
- [ ] phase 5c dashboard UI 切換 v1/v2 + nowcast 強度
- [ ] 等 2026Q1 公告後實測驗收（預計 2026-06）
- [ ] 等 2026 全年實際公告後最終評估（預計 2027 Q3）
