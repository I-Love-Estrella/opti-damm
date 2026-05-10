"""Head-to-head batch comparison: TWO algorithms on the SAME case set.

Sister of multi_run.py:
  - multi_run     → 1 algorithm  × N cases → distribution per metric
  - multi_compare → 2 algorithms × N cases → paired deltas + winner stats

The "paired" part matters: every metric is compared per case (algoB vs
algoA on the same date+ruta), so route difficulty drops out and we get
a clean A-vs-B effect even on a small sample.
"""

from __future__ import annotations

import datetime as dt
import statistics
import time
from dataclasses import dataclass, field

from simulator.algorithms import REGISTRY
from simulator.bench.multi_run import (
    CaseRef,
    CaseResult,
    MultiRunStats,
    _AGG_KEYS,
    _percentile,
    _summarize,
    _sample_cases,
    run_multi,
)
from simulator.core.simulator import Simulator
from simulator.data.catalog import Catalog
from simulator.data.clients import Clients
from simulator.data.loader import load_all
from simulator.data.network import Network
from simulator.data.orders import DayCaseBuilder


# Metrics where LOWER is better (delta < 0 means algoB wins).
# Anything not listed defaults to higher-is-better.
_LOWER_BETTER: frozenset[str] = frozenset({
    "total_minutes",
    "drive_minutes",
    "service_minutes",
    "depot_minutes",
    "overhead_minutes",
    "driver_minutes",
    "total_km",
    "fuel_eur",
    "labor_eur",
    "driver_labor_eur",
    "depot_labor_eur",
    "total_cost_eur",
    "co2_kg",
    "search_moves",
    "tw_violations_min",
    "drops",
    "placement_rejections",
    "lost_units",
})


@dataclass(frozen=True)
class CompareCase:
    """One paired result on a single case."""
    date: str
    ruta: str
    a_kpis: dict | None
    b_kpis: dict | None
    a_success: bool
    b_success: bool
    a_physics: int
    b_physics: int
    a_val_errors: int
    b_val_errors: int
    a_elapsed_sec: float
    b_elapsed_sec: float
    a_truck: str = ""
    b_truck: str = ""

    def to_dict(self) -> dict:
        return {
            "date": self.date,
            "ruta": self.ruta,
            "a": {
                "kpis": self.a_kpis,
                "success": self.a_success,
                "physics_violations": self.a_physics,
                "validation_errors": self.a_val_errors,
                "elapsed_sec": round(self.a_elapsed_sec, 4),
                "truck": self.a_truck,
            },
            "b": {
                "kpis": self.b_kpis,
                "success": self.b_success,
                "physics_violations": self.b_physics,
                "validation_errors": self.b_val_errors,
                "elapsed_sec": round(self.b_elapsed_sec, 4),
                "truck": self.b_truck,
            },
        }


@dataclass(frozen=True)
class MetricComparison:
    """Aggregate paired comparison for one numeric KPI."""
    metric: str
    n_paired: int
    a_mean: float
    b_mean: float
    delta_mean: float          # mean(B - A)
    delta_pct_mean: float      # mean( (B - A) / A * 100 ); 0 when A==0
    delta_median: float
    delta_min: float
    delta_max: float
    a_wins: int                # B is better than A on this metric
    b_wins: int
    ties: int
    lower_better: bool

    def to_dict(self) -> dict:
        return {
            "metric": self.metric,
            "n_paired": self.n_paired,
            "a_mean": round(self.a_mean, 4),
            "b_mean": round(self.b_mean, 4),
            "delta_mean": round(self.delta_mean, 4),
            "delta_pct_mean": round(self.delta_pct_mean, 2),
            "delta_median": round(self.delta_median, 4),
            "delta_min": round(self.delta_min, 4),
            "delta_max": round(self.delta_max, 4),
            "a_wins": self.a_wins,
            "b_wins": self.b_wins,
            "ties": self.ties,
            "lower_better": self.lower_better,
        }


@dataclass(frozen=True)
class CompareReport:
    algo_a: str
    algo_b: str
    n_cases: int
    n_paired: int                  # both ran successfully on the same case
    a_only_success: int            # cases A ran clean, B failed
    b_only_success: int            # cases B ran clean, A failed
    both_failed: int
    a_stats: MultiRunStats
    b_stats: MultiRunStats
    metrics: list[MetricComparison] = field(default_factory=list)
    cases: list[CompareCase] = field(default_factory=list)
    duration_sec: float = 0.0

    def to_dict(self) -> dict:
        return {
            "algo_a": self.algo_a,
            "algo_b": self.algo_b,
            "n_cases": self.n_cases,
            "n_paired": self.n_paired,
            "a_only_success": self.a_only_success,
            "b_only_success": self.b_only_success,
            "both_failed": self.both_failed,
            "a_stats": self.a_stats.to_dict(),
            "b_stats": self.b_stats.to_dict(),
            "metrics": [m.to_dict() for m in self.metrics],
            "cases": [c.to_dict() for c in self.cases],
            "duration_sec": round(self.duration_sec, 3),
        }


def _build_pairs(
    a_results: list[CaseResult],
    b_results: list[CaseResult],
) -> list[CompareCase]:
    """Join the two per-case result lists by (date, ruta)."""
    a_by_key = {(c.date, c.ruta): c for c in a_results}
    b_by_key = {(c.date, c.ruta): c for c in b_results}
    keys = sorted(set(a_by_key) | set(b_by_key))
    out: list[CompareCase] = []
    for k in keys:
        a = a_by_key.get(k)
        b = b_by_key.get(k)
        out.append(
            CompareCase(
                date=k[0],
                ruta=k[1],
                a_kpis=(a.kpis if a else None),
                b_kpis=(b.kpis if b else None),
                a_success=bool(a and a.success),
                b_success=bool(b and b.success),
                a_physics=int(a.physics_violations if a else 0),
                b_physics=int(b.physics_violations if b else 0),
                a_val_errors=int(a.validation_errors if a else 0),
                b_val_errors=int(b.validation_errors if b else 0),
                a_elapsed_sec=float(a.elapsed_sec if a else 0.0),
                b_elapsed_sec=float(b.elapsed_sec if b else 0.0),
                a_truck=str(a.truck_code if a else ""),
                b_truck=str(b.truck_code if b else ""),
            )
        )
    return out


def _compute_metric_table(cases: list[CompareCase]) -> list[MetricComparison]:
    """For each numeric KPI, compute paired comparison stats over the
    cases where BOTH algorithms produced a result."""
    out: list[MetricComparison] = []
    for metric in _AGG_KEYS:
        a_vals: list[float] = []
        b_vals: list[float] = []
        deltas: list[float] = []
        delta_pcts: list[float] = []
        a_wins = b_wins = ties = 0
        lower_better = metric in _LOWER_BETTER
        for c in cases:
            if not c.a_kpis or not c.b_kpis:
                continue
            a_v = c.a_kpis.get(metric)
            b_v = c.b_kpis.get(metric)
            if not isinstance(a_v, (int, float)) or not isinstance(b_v, (int, float)):
                continue
            if a_v != a_v or b_v != b_v:  # NaN
                continue
            a_vals.append(float(a_v))
            b_vals.append(float(b_v))
            d = float(b_v) - float(a_v)
            deltas.append(d)
            if abs(a_v) > 1e-9:
                delta_pcts.append((d / float(a_v)) * 100.0)
            else:
                delta_pcts.append(0.0)
            tol = 1e-6
            if abs(d) <= tol:
                ties += 1
            elif (d < 0 and lower_better) or (d > 0 and not lower_better):
                b_wins += 1
            else:
                a_wins += 1
        if not deltas:
            continue
        out.append(
            MetricComparison(
                metric=metric,
                n_paired=len(deltas),
                a_mean=statistics.fmean(a_vals),
                b_mean=statistics.fmean(b_vals),
                delta_mean=statistics.fmean(deltas),
                delta_pct_mean=statistics.fmean(delta_pcts),
                delta_median=statistics.median(deltas),
                delta_min=min(deltas),
                delta_max=max(deltas),
                a_wins=a_wins,
                b_wins=b_wins,
                ties=ties,
                lower_better=lower_better,
            )
        )
    return out


def run_compare(
    algo_a: str,
    algo_b: str,
    *,
    cases: list[CaseRef] | None = None,
    sample: str | None = None,
    n: int = 10,
    min_clients: int = 5,
    seed: int = 42,
    truck_code: str | None = None,
    builder: DayCaseBuilder | None = None,
    clients: Clients | None = None,
    network: Network | None = None,
    sim: Simulator | None = None,
) -> CompareReport:
    """Run two algorithms on the same case set, return paired comparison."""
    if algo_a not in REGISTRY:
        raise ValueError(f"Unknown algorithm: {algo_a}")
    if algo_b not in REGISTRY:
        raise ValueError(f"Unknown algorithm: {algo_b}")
    if algo_a == algo_b:
        raise ValueError("algo_a and algo_b must differ — pick two algorithms")

    if builder is None or clients is None or network is None or sim is None:
        raw = load_all()
        catalog = Catalog.build(raw)
        clients = clients or Clients.build(raw)
        builder = builder or DayCaseBuilder(raw, catalog, clients)
        network = network or Network()
        sim = sim or Simulator(clients=clients, network=network)

    # Resolve the case list ONCE so both algorithms run on the same days.
    case_refs = _sample_cases(
        builder,
        cases=cases,
        sample=sample,
        n=n,
        min_clients=min_clients,
        seed=seed,
    )

    t0 = time.perf_counter()
    a_stats = run_multi(
        algo_a,
        cases=case_refs,
        truck_code=truck_code,
        builder=builder, clients=clients, network=network, sim=sim,
    )
    b_stats = run_multi(
        algo_b,
        cases=case_refs,
        truck_code=truck_code,
        builder=builder, clients=clients, network=network, sim=sim,
    )

    pairs = _build_pairs(a_stats.cases, b_stats.cases)
    metrics = _compute_metric_table(pairs)

    n_paired = sum(1 for c in pairs if c.a_kpis and c.b_kpis)
    a_only = sum(
        1 for c in pairs if c.a_success and not c.b_success
    )
    b_only = sum(
        1 for c in pairs if c.b_success and not c.a_success
    )
    both_failed = sum(
        1 for c in pairs if not c.a_success and not c.b_success
    )

    return CompareReport(
        algo_a=algo_a,
        algo_b=algo_b,
        n_cases=len(case_refs),
        n_paired=n_paired,
        a_only_success=a_only,
        b_only_success=b_only,
        both_failed=both_failed,
        a_stats=a_stats,
        b_stats=b_stats,
        metrics=metrics,
        cases=pairs,
        duration_sec=time.perf_counter() - t0,
    )
