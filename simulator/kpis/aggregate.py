"""Aggregate KPIs across (day × algorithm) runs."""

from __future__ import annotations

from collections.abc import Iterable

import pandas as pd

from simulator.kpis.metrics import DayKpis


def to_dataframe(records: Iterable[DayKpis]) -> pd.DataFrame:
    return pd.DataFrame([r.to_dict() for r in records])


def summary(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    numeric = df.select_dtypes(include="number").columns.tolist()
    grouped = df.groupby("algorithm")[numeric].agg(["mean", "std", "min", "max"])
    grouped.columns = ["__".join(col).strip("_") for col in grouped.columns.values]
    return grouped.reset_index()


def head_to_head(df: pd.DataFrame, baseline: str) -> pd.DataFrame:
    if df.empty:
        return df
    base = df[df["algorithm"] == baseline].set_index(["date", "ruta"])
    rows: list[dict] = []
    for algo in df["algorithm"].unique():
        if algo == baseline:
            continue
        sub = df[df["algorithm"] == algo].set_index(["date", "ruta"])
        joined = sub.join(base, lsuffix="_algo", rsuffix="_base", how="inner")
        for col in ["total_minutes", "total_km", "search_moves", "total_cost_eur", "co2_kg"]:
            ac = f"{col}_algo"
            bc = f"{col}_base"
            if ac in joined.columns and bc in joined.columns:
                rows.append({
                    "algorithm": algo,
                    "metric": col,
                    "delta_avg": (joined[ac] - joined[bc]).mean(),
                    "delta_pct_avg": ((joined[ac] - joined[bc]) / joined[bc].replace(0, 1)).mean() * 100,
                    "wins": int((joined[ac] < joined[bc]).sum()),
                    "losses": int((joined[ac] > joined[bc]).sum()),
                    "ties": int((joined[ac] == joined[bc]).sum()),
                })
    return pd.DataFrame(rows)
