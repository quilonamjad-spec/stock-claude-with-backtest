"""
scan_pipeline.py
-----------------
Ties data.py (fetching) and indicators.py (evaluation) together into the
one scan pipeline both Stage 1's live scan and Stage 5's historical
backtest call — so there's exactly one place this logic lives.
"""

import pandas as pd
import streamlit as st

from config import IST_TZ, SECTOR_INDICES, DEFAULT_SECTOR, DEFAULT_INDEX_LABEL
from data import fetch_batch, chunk, get_symbol_sector_map, compute_index_pct_changes
from indicators import build_result


def run_scan_pipeline(
    symbols: list, as_of, lookback_days: int, batch_size: int,
    progress_prefix: str = "", show_progress: bool = True,
) -> pd.DataFrame:
    """Batched fetch -> per-symbol condition evaluation -> sector-index
    alignment, for a given symbol universe and as_of timestamp. Returns an
    empty DataFrame if nothing matched. This is the exact same logic Stage 1
    uses, factored out so Stage 5's multi-day backtest can call it without
    duplicating the fetch/evaluate loop."""
    period = f"{lookback_days}d"
    results = []
    progress = st.progress(0.0, text=f"{progress_prefix}Downloading & scanning...") if show_progress else None
    total_batches = max(1, (len(symbols) + batch_size - 1) // batch_size)

    for i, group in enumerate(chunk(symbols, batch_size)):
        try:
            batch_df = fetch_batch(tuple(group), period=period, interval="5m")
        except Exception:
            if progress:
                progress.progress((i + 1) / total_batches)
            continue

        for sym in group:
            try:
                if len(group) == 1:
                    sdf = batch_df
                else:
                    if sym not in batch_df.columns.get_level_values(0):
                        continue
                    sdf = batch_df[sym].dropna(how="all")
                if sdf.empty:
                    continue
                if sdf.index.tz is None:
                    sdf.index = sdf.index.tz_localize(IST_TZ)
                else:
                    sdf.index = sdf.index.tz_convert(IST_TZ)
                res = build_result(sym, sdf, as_of)
                if res:
                    results.append(res)
            except Exception:
                continue

        if progress:
            progress.progress((i + 1) / total_batches, text=f"{progress_prefix}Scanned batch {i + 1}/{total_batches}")

    if progress:
        progress.empty()

    if not results:
        return pd.DataFrame()

    sector_map = get_symbol_sector_map()
    index_changes = compute_index_pct_changes(as_of, lookback_days)
    for r in results:
        sector = sector_map.get(r["Symbol"], DEFAULT_SECTOR)
        idx_label = SECTOR_INDICES.get(sector, {}).get("label", DEFAULT_INDEX_LABEL)
        idx_pct = index_changes.get(sector, index_changes.get(DEFAULT_SECTOR))
        r["Index"] = idx_label
        r["Index % Chg"] = idx_pct
        r["Aligned"] = (
            None if idx_pct is None
            else ("✅" if (r["% Change"] >= 0) == (idx_pct >= 0) else "❌")
        )

    return pd.DataFrame(results)
