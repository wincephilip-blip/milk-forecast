"""一次性工具：從 DHI xlsx 提取每年產乳牛獨立頭數，存成 JSON 快取。"""
import json
import logging
from pathlib import Path
import pandas as pd
from .. import config

log = logging.getLogger("milkfc.cow_count")

CACHE_PATH = config.SNAPSHOT_DIR / "_dhi_yearly_cows.json"


def extract_dhi_yearly_cows(years: list = None,
                              raw_dir: Path = None,
                              force: bool = False) -> dict:
    """
    Returns: {year: {n_cows, n_farms, n_records, dhi_total_kg}}
    每處理完一年就存檔（容錯：中途掛掉也能 resume）。
    """
    raw_dir = raw_dir or (config.ROOT / "raw_data")
    out = {}
    if CACHE_PATH.exists():
        cached = json.loads(CACHE_PATH.read_text())
        out = {int(k): v for k, v in cached.items()}
        if not force and years is None:
            years = list(range(2015, 2026))
        elif not force and all(y in out for y in (years or [])):
            return out

    years = years or list(range(2015, 2026))
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)

    for y in years:
        if y in out and not force:
            log.info(f"  {y}: 已快取、跳過")
            continue
        fp = raw_dir / f"{y}dhi.xlsx"
        if not fp.exists():
            log.info(f"  {y}: 無檔案、跳過")
            continue
        log.info(f"  Reading {fp.name}...")
        df = pd.read_excel(fp, usecols=["統一編號", "乳量", "酪農代號"])
        df = df[df["乳量"].notna() & (df["乳量"] > 0)]
        out[y] = {
            "n_cows": int(df["統一編號"].nunique()),
            "n_farms": int(df["酪農代號"].nunique()),
            "n_records": int(len(df)),
            "dhi_total_kg": float(df["乳量"].sum()),
        }
        # 立刻存（每年都 checkpoint）
        CACHE_PATH.write_text(json.dumps(out, indent=2, ensure_ascii=False))
        log.info(f"  {y}: 場={out[y]['n_farms']} 牛={out[y]['n_cows']:,} (saved)")

    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                          format="%(message)s")
    out = extract_dhi_yearly_cows()
    for y, info in sorted(out.items()):
        print(f"{y}: 場={info['n_farms']:>4} 產乳牛={info['n_cows']:>7,} "
              f"記錄={info['n_records']:>8,} DHI乳量(公噸)={info['dhi_total_kg']/1000:>10,.1f}")
