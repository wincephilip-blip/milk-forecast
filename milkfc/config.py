"""集中式設定。生產環境應改用 yaml/env 變數。"""
import os
from pathlib import Path

# === 路徑 ===
# 優先讀環境變數 MILKFC_ROOT，否則用模組相對位置
_ROOT_ENV = os.environ.get("MILKFC_ROOT")
if _ROOT_ENV:
    ROOT = Path(_ROOT_ENV)
else:
    # milkfc/config.py → ../  (套件父目錄即專案根)
    ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "raw_data"
SNAPSHOT_DIR = ROOT / "snapshots"
REPORT_DIR = ROOT / "reports"

# === DHI 欄位重命名（資料合約）===
COLUMN_MAP = {
    "資料年度":"year","資料月份":"month","酪農代號":"farm_id","場內編號":"ear_tag",
    "統一編號":"cow_id","父親牛精液編號":"sire_id","母親牛統一編號":"dam_id",
    "出生日期":"birth_date","胎次":"parity","天數":"dim","乳量":"milk_kg",
    "脂肪率":"fat_pct","蛋白質率":"protein_pct","乳糖率":"lactose_pct",
    "體細胞數":"scc","乳量305":"milk_305","最近分娩日期":"last_calving_date",
    "採樣日期":"sample_date","月齡":"age_month","檢測日期":"test_date",
    "最後配種日期":"last_breeding_date","最後配種精液":"last_breeding_semen",
    "配種次數":"breeding_count","前次分娩日期":"prev_calving_date",
    "第一次配種日期":"first_breeding_date","第一次配種精液":"first_breeding_semen",
}

REQUIRED_COLUMNS = [
    "farm_id","cow_id","year","month","sample_date",
    "parity","dim","milk_kg","last_calving_date","last_breeding_date"
]

# === 資料驗證閾值 ===
MIN_RECORDS_PER_FILE = 50_000        # 一年 DHI 應該 >50k 筆
MIN_FILL_RATE = {                    # 各欄位最低填寫率
    "milk_kg": 0.95,
    "dim": 0.95,
    "parity": 0.95,
    "last_calving_date": 0.85,
    "last_breeding_date": 0.40,      # 配種紀錄略寬鬆
}
DATA_FRESHNESS_DAYS = 60             # 最新紀錄不應超過 60 天前

# === 模型參數 ===
GESTATION_DAYS = 280
DRY_OFF_DAYS = 60                    # 分娩前 60 天乾乳
PARITY_GROUPS = [1, 2, 3, 4]         # 4+ 合併
WOOD_DIM_RANGE = (5, 365)
MILK_RANGE = (1, 80)
PARITY_RANGE = (1, 10)

# === 預測參數 ===
HORIZON_MONTHS = 12
N_SIMULATIONS = 30
N_BOOTSTRAP = 50                     # Wood 參數 bootstrap 次數（v2: 50）
ACTIVE_LOOKBACK_DAYS = 180           # 活躍牛定義: 過去 N 天有測乳
TRAIN_WINDOW_MONTHS = 48             # 訓練視窗預設值（驗證：48 月最佳）
AUTO_WINDOW = True                   # 是否對每場自動選最佳視窗
AUTO_WINDOW_CANDIDATES = [24, 36, 48, 60]  # 候選視窗

# === 異常偵測 ===
ANOMALY_BIAS_THRESHOLD = 20.0        # 偏差 > ±20% 觸發
ANOMALY_CONSEC_MONTHS = 2            # 連續 N 月觸發告警

# === 儀表板 ===
DASHBOARD_OUT = ROOT / "dashboard.html"
ARCHIVE_DASHBOARD_DIR = ROOT / "reports"
