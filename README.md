# milkfc — DHI 乳量預測 Pipeline (Phase 2 v0.1)

主管機關用的台灣 DHI 月度乳量預測系統。把 DHI 紀錄轉成「未來 12 個月」場級與加總級乳量預測（含 P10–P90 不確定區間、異常場自動偵測、版本控管）。

## 套件結構

```
milkfc/
├── __init__.py            # 套件版本資訊
├── config.py              # 集中式設定（路徑、閾值、模型參數）
├── __main__.py            # CLI 入口
├── pipeline.py            # 主 orchestrator
├── data/
│   ├── loader.py          # DHI 檔案載入
│   └── validator.py       # 資料合約驗證
├── models/
│   ├── lactation.py       # Wood 乳期曲線擬合
│   ├── events.py          # 分娩、配種、後備、成長率
│   └── trainer.py         # 整合訓練
├── forecast/
│   ├── simulator.py       # 單頭牛蒙地卡羅
│   └── bootstrap.py       # 參數 bootstrap + 全場加總
├── diagnostics/
│   ├── anomaly.py         # 異常場偵測
│   └── backtest.py        # 回測 MAPE/bias/coverage
└── dashboard/
    └── builder.py         # 從快照產生 HTML
```

## 使用方式

```bash
cd /Users/tu/Milk_forecast
source .venv/bin/activate

# 顯示版本
python3 -m milkfc --version

# === 月度一鍵全跑（推薦）===
python3 -m milkfc monthly                  # 預測 + 月度分布 + 泌乳曲線 + 告警
python3 -m milkfc monthly --skip-analyze   # 只要預測，不要描述性分析

# === 個別命令（手動精細控制時用）===
python3 -m milkfc validate                          # 資料驗證
python3 -m milkfc run --dashboard                   # 只跑預測
python3 -m milkfc analyze --view all                # 只產月度分布+泌乳曲線
python3 -m milkfc analyze --view seasonal           # 只產月度分布
python3 -m milkfc analyze --view lactation          # 只產泌乳曲線
python3 -m milkfc analyze --year-range 2022-2024    # 限制年度範圍

# === 輔助 ===
python3 -m milkfc list-snaps                        # 列出快照
python3 -m milkfc status                            # 最近快照
python3 -m milkfc diagnose                          # 異常告警
```

## 三組儀表板

| 儀表板 | 用途 | 對象 |
|---|---|---|
| `dashboard.html` | 未來預測 + 歷史驗證 | 每月看趨勢、決策 |
| `seasonal.html` | 各場各年 1-12 月乳量分布 | 看季節型態、跨場比較 |
| `lactation.html` | 場別/全國/個體牛泌乳曲線 | 看典型樣貌、KPI 比較 |

每個 HTML 頁首都有導覽列可互相切換。

## 月度自動執行

加到 crontab：
```
0 3 5 * * /Users/tu/Milk_forecast/scripts/run_monthly.sh
```

每月 5 日 03:00 自動執行（DHI 通常月初發布，留 5 天緩衝）。Log 會寫到 `logs/run_YYYYMMDD.log`，自動保留 90 天。

## 資料合約

放在 `raw_data/` 目錄下的 `*.xlsx`，每年一檔，需含以下欄位（中文）：

| 必要欄位 | 用途 |
|---|---|
| 統一編號 | 牛 ID |
| 酪農代號 | 場 ID |
| 採樣日期 | 測乳日期 |
| 乳量 | 日乳量 (kg) |
| 胎次 | parity |
| 天數 | DIM |
| 最近分娩日期 | 用於重建分娩事件流 |
| 最後配種日期 | 用於預測下次分娩 |

驗證層會在每次執行前檢查欄位完整性與資料新鮮度（門檻見 `config.py`）。

## 預測快照

每次 `run` 會在 `snapshots/<timestamp>/` 寫入：

```
snapshots/20260429T141032/
├── manifest.json          # 元資料：時間、版本、資料 hash、設定、警告
├── results.pkl            # 完整結果（dataframe）
└── forecasts.csv          # 預測明細（給 audit）
```

每個快照含資料 hash + 模型版本 → **任何預測都可回溯重現**，是政府工具的稽核基礎。

## 異常告警

連續 2 個月實際 vs 預測 P50 偏差超過 ±20% 時自動標記。在儀表板上會以紅色 banner 顯示，並由 `python3 -m milkfc diagnose` 列出細節。

## 設定（config.py）

```python
# 資料驗證閾值
MIN_RECORDS_PER_FILE = 50_000
DATA_FRESHNESS_DAYS = 60

# 模型參數
GESTATION_DAYS = 280
DRY_OFF_DAYS = 60

# 預測
HORIZON_MONTHS = 12
N_SIMULATIONS = 20
N_BOOTSTRAP = 10

# 異常閾值
ANOMALY_BIAS_THRESHOLD = 20.0
ANOMALY_CONSEC_MONTHS = 2
```

可用環境變數 `MILKFC_ROOT` 覆寫專案根目錄。

## 故障排除

**Q: 跑完沒看到儀表板？**
A: 確認加了 `--dashboard` 旗標。儀表板在 `/Users/tu/Milk_forecast/dashboard.html`。

**Q: 驗證階段一直 fail？**
A: 看 log 中 `[VALIDATION]` 的訊息——通常是欄位名稱改變或資料延遲。`config.COLUMN_MAP` 控制欄位對應。

**Q: 某場 MAPE 突然飆高？**
A: 用 `python3 -m milkfc diagnose` 看連續偏差月份。多半是場內管理變動，並非模型錯誤——這正是異常告警設計目的。

**Q: 跑起來太慢？**
A: 場數多時建議分批用 `--farms`，或調低 `N_BOOTSTRAP`/`N_SIMULATIONS`。每場 ~5–15 秒。

## 版本

- 套件版本: 0.2.0
- 模型版本: wood-loglinear-v3-segmented
- Phase 2.5 階段: 改進完成（向量化、性控偵測、場別分類）

### Phase 2.5 改進 (v0.2)

1. **模擬器向量化**：用 numpy 取代 pd.Timedelta 逐日迴圈，預期 5-50x 速度提升（大場特別有感）
2. **Edge case 防護**：合併模式 fc 為空時自動跳過（不再 crash）
3. **不確定性區間擴大**：bootstrap 從 10 提到 50、模擬從 20 提到 30；P10–P90 命中率預期 70%+
4. **性控精液偵測** (`milkfc/models/sexed_semen.py`)：辨識 NAAB sexed semen 代碼（501/507/511/250 等）、調整後備母牛入場率
5. **場別四維度分類** (`milkfc/segmentation.py`)：規模 / 場齡 / 趨勢 / 區域，產生 245 個 segment
6. **分群 prior 平滑**：小場資料不足時自動向所屬 segment 平均收斂（shrinkage 0.15-0.5）
7. **Farm.xlsx 整合** (`milkfc/data/farm_meta.py`)：載入 732 場後設資料
8. **官方統計校正** (`milkfc/data/national_stats.py` + `milkfc/calibration.py`)：解析農業部畜禽季報、計算 DHI 涵蓋率、提供「全國估計」與「按縣市/區域」加總視圖

### 官方季報資料更新

每季從 [農業統計資料查詢](https://agrstat.moa.gov.tw/sdweb/public/book/Book.aspx) 下載最新「畜禽統計調查結果」:
- 表2 在養按品項分.xlsx → 放進 `raw_data/`
- 系統會自動偵測最新一份做校正

## Phase 3 路線圖（尚未實作）

- 資料庫後端（PostgreSQL）取代 pickle 快照
- 真正的 web 服務（FastAPI + 前端框架）取代靜態 HTML
- 角色權限（產業觀察者 / 場別內部 / 政策模擬者）
- 政策情境模擬器（補貼後備牛 N 頭 → 12 月供應）
- 規模指標自動接入畜禽場登記資料
