# 2026 預測驗收 Runbook

**目的**：對 cohort v1（論文版本）與 v2（工程改善版本）之 2026 年預測做時序滾動驗證，最終確認 v2 改造之實際成效。

**腳本**：`reports/validate_2026.py`

**儀表板**：每次執行後產生 `snapshots/_validation_2026_{date}.json`

---

## 時間節點與動作

### A. 即時（2026-01 ~ 2026-05）：建立基線

```bash
python3 reports/validate_2026.py --as-of 2026-05-09
```

**狀態**：無 target year 季報、純外推。

**目前結果**（2026-05-09）：

| 版本 | 預測 | 萬公噸 |
|------|------|--------|
| v1 simple（論文版本） | 499,236 公噸 | 49.92 |
| v2 phase 3 純外推 | 472,308 公噸 | 47.23 |
| **v2 phase 4 純外推（推薦）** | **460,119 公噸** | **46.01** |

預測值差距達 8.5%，反映 v1 與 v2 對 2024 政策衝擊後 N 走勢之不同假設。

---

### B. 2026-06（2026Q1 季報公告後，第一次 nowcast）

**前置**：確認 cache 已更新 2026Q1 資料。
```bash
# 檢查 cache 狀態
python3 -c "
from milkfc.data.quarterly_inventory import QUARTERLY_INVENTORY
keys = sorted(QUARTERLY_INVENTORY.keys())
print('latest quarter:', keys[-1])
assert '2026Q1' in QUARTERLY_INVENTORY, 'need to add 2026Q1 to cache'
print('2026Q1:', QUARTERLY_INVENTORY['2026Q1'])
"
```

**執行**：
```bash
python3 reports/validate_2026.py --as-of 2026-07-01
```

**預期**：v2 phase 4 (auto) 會自動偵測 2026Q1 真值並啟用 nowcast。
- 若 2026Q1 N ≈ 56,000–58,000：v2 預測落在 45–47 萬公噸範圍
- 若 2026Q1 N 突跳（政策重大調整）：v2 預測會跟著調整

**追蹤指標**：
- v1 vs v2 phase 4 預測差距（過去是 8.5%、有 nowcast 後應收斂）
- N 投影誤差（從 2026Q1 真值 vs phase 4 線性外推 Q1 之差距）

---

### C. 2026-09 / 2026-12（Q2、Q3 公告後）

```bash
python3 reports/validate_2026.py --as-of 2026-09-01   # Q1+Q2 nowcast
python3 reports/validate_2026.py --as-of 2026-12-01   # Q1+Q2+Q3 nowcast
```

**追蹤**：v2 預測值是否單調收斂（理論上 nowcast 越多越接近真值）。如果不單調、檢查 cache 是否有資料修正、季報是否被覆蓋更新。

---

### D. 2027-Q3（2026 年報公告後，最終驗收）

**前置**：2026 年報通常於 2027 Q3 前公告。確認 raw_data 已更新。

**執行**：
```bash
python3 reports/validate_2026.py --final
```

**輸出**：v1、v2 phase 3、v2 phase 4 各情境（不同 as_of_date）之點估計與 2026 實際產量比對表。

**驗收成功標準**（以 v2 phase 4 + auto nowcast 為準）：

| 階段 | 預期誤差 |
|------|---------|
| 純外推（A 階段）| ≤ ±5% |
| Q1 nowcast（B 階段）| ≤ ±3% |
| Q1+Q2+Q3 nowcast（C 階段，if available）| ≤ ±2% |

任一階段超過上限：
1. 檢查 cache 完整性（季度與年度資料是否齊全）
2. 比對該年是否有重大政策事件（如 2024 養牛計畫第二階段）
3. 若係結構性原因，更新 v2 演算法（例如加入政策虛擬變數）並重跑歷史 backtest 確認改善

---

## Q1 nowcast 可能無法完美的情境

1. **季報資料延遲**：農業部統計處有時 Q1 季報延至 6 月底才公告，導致 Q2 末仍只能純外推
2. **季報資料修正**：偶有「Q1 數字後續修正」情況，導致原始 nowcast 預測與後續修正版本不一致——這是正常的、保留原始 snapshot 即可
3. **政策第二階段**：2026 年若養牛計畫 phase 2 啟動（例：再加碼淘汰 5,000 頭），N 會出現第二次跳動，nowcast 也只能即時跟進、無法事先預知

---

## Output 文件

- `snapshots/_validation_2026_20260509.json` ← 第一次基線（已產生）
- `snapshots/_validation_2026_20260701.json` ← 計畫於 2026-06 後產生
- `snapshots/_validation_2026_20260901.json` ← 計畫於 2026-09 後產生
- `snapshots/_validation_2026_20261201.json` ← 計畫於 2026-12 後產生
- `snapshots/_validation_2026_final.json` ← 計畫於 2027 Q3 產生

每份 JSON 含：as_of_date、target_year、各版本 predictions（含 N、Q、r 子值）、final 模式下含 actual 與 err_pct。

---

## 與論文之關係

**論文不需修改**——v1 simple 之 2.15% MAPE 仍正確，論文是「截至 2024 年資料完成、純後驗回測」的研究結論。

**v2 屬實務工程改善**，2026 預測驗收結果可作為下一期論文（多年期實證 + 工程改善）之素材，但須等 2027 Q3 final 驗收完成後再決定是否撰寫。
