"""Run ONE algorithm against MANY (date, ruta) cases and aggregate stats.

Sister of bench/runner.py — that one runs N×M (cases × algorithms); this one
focuses on a single algorithm so we can drill into its error rate and
distribution of times/costs across many real days.

Stats produced (per numeric KPI): sum, mean, median, std, min, max, p95.
Plus error/violation tallies and a per-case capsule for tabular display.
"""

from __future__ import annotations

import datetime as dt
import statistics
import time
from dataclasses import dataclass, field

from simulator.algorithms import REGISTRY, get
from simulator.config import REPORTS_DIR
from simulator.core.simulator import Simulator
from simulator.data.catalog import Catalog
from simulator.data.clients import Clients
from simulator.data.loader import load_all
from simulator.data.network import Network
from simulator.data.orders import DayCaseBuilder
from simulator.kpis.metrics import compute
from simulator.validation import validate_plan


# Numeric KPI fields we want a full distribution for. Other DayKpis fields
# (n_clients_*, success flags, strings) are passed through but not summarized.
_AGG_KEYS: tuple[str, ...] = (
    "total_minutes",
    "drive_minutes",
    "service_minutes",
    "overhead_minutes",
    "total_km",
    "fuel_eur",
    "labor_eur",
    "total_cost_eur",
    "co2_kg",
    "search_moves",
    "tw_violations_min",
    "drops",
    "fill_rate",
    "pallet_volume_util",
    "weight_util",
    "returnables_picked_units",
    "placement_rejections",
    "lost_units",
)


@dataclass(frozen=True)
class CaseRef:
    date: dt.date
    ruta: str


@dataclass(frozen=True)
class CaseResult:
    date: str
    ruta: str
    success: bool
    error: str | None
    fit_ok: bool
    validation_errors: int
    validation_warnings: int
    physics_violations: int
    kpis: dict | None
    elapsed_sec: float
    # Compact list of validator issues for this case — every ERROR / WARNING
    # / INFO row produced by validate_plan, kept lightweight for transport.
    # Each item: {severity, code, message, where}.
    issues: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "date": self.date,
            "ruta": self.ruta,
            "success": self.success,
            "error": self.error,
            "fit_ok": self.fit_ok,
            "validation_errors": self.validation_errors,
            "validation_warnings": self.validation_warnings,
            "physics_violations": self.physics_violations,
            "kpis": self.kpis,
            "elapsed_sec": round(self.elapsed_sec, 4),
            "issues": list(self.issues),
        }


@dataclass(frozen=True)
class MultiRunStats:
    algorithm: str
    n_cases: int
    n_success: int
    n_failed: int
    n_invalid_plan: int           # success=True but validator flagged ERRORs
    n_with_physics: int           # success=True but had ≥1 physics violation
    total_physics_violations: int
    total_validation_errors: int
    total_validation_warnings: int
    total_drops: int
    total_capacity_violations: int
    aggregates: dict              # KPI -> {sum, mean, median, stdev, min, max, p95, n}
    cases: list[CaseResult] = field(default_factory=list)
    failures: list[dict] = field(default_factory=list)
    duration_sec: float = 0.0

    def to_dict(self) -> dict:
        return {
            "algorithm": self.algorithm,
            "n_cases": self.n_cases,
            "n_success": self.n_success,
            "n_failed": self.n_failed,
            "n_invalid_plan": self.n_invalid_plan,
            "n_with_physics": self.n_with_physics,
            "total_physics_violations": self.total_physics_violations,
            "total_validation_errors": self.total_validation_errors,
            "total_validation_warnings": self.total_validation_warnings,
            "total_drops": self.total_drops,
            "total_capacity_violations": self.total_capacity_violations,
            "success_rate": round(
                (self.n_success / self.n_cases) if self.n_cases else 0.0, 4
            ),
            "clean_rate": round(
                (
                    (self.n_success - self.n_invalid_plan - self.n_with_physics)
                    / self.n_cases
                )
                if self.n_cases
                else 0.0,
                4,
            ),
            "aggregates": self.aggregates,
            "cases": [c.to_dict() for c in self.cases],
            "failures": list(self.failures),
            "duration_sec": round(self.duration_sec, 3),
        }


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    s = sorted(values)
    k = (len(s) - 1) * pct
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    frac = k - lo
    return s[lo] * (1 - frac) + s[hi] * frac


def _summarize(values: list[float]) -> dict:
    if not values:
        return {"n": 0, "sum": 0.0, "mean": 0.0, "median": 0.0,
                "stdev": 0.0, "min": 0.0, "max": 0.0, "p95": 0.0}
    return {
        "n": len(values),
        "sum": round(sum(values), 4),
        "mean": round(statistics.fmean(values), 4),
        "median": round(statistics.median(values), 4),
        "stdev": round(statistics.pstdev(values), 4) if len(values) > 1 else 0.0,
        "min": round(min(values), 4),
        "max": round(max(values), 4),
        "p95": round(_percentile(values, 0.95), 4),
    }


def _sample_cases(
    builder: DayCaseBuilder,
    *,
    cases: list[CaseRef] | None,
    sample: str | None,
    n: int,
    min_clients: int,
    seed: int,
) -> list[CaseRef]:
    """Resolve the case list. Either explicit `cases`, or a sampling rule
    over the full catalog filtered by min_clients."""
    if cases:
        return list(cases)

    df = builder.list_day_cases(min_clients=min_clients)
    if df.empty:
        return []

    if sample == "all":
        chosen = df
    elif sample == "first":
        chosen = df.head(max(1, n))
    elif sample == "random":
        k = min(max(1, n), len(df))
        chosen = df.sample(k, random_state=seed).sort_values(["fecha", "Ruta"])
    else:
        chosen = df.head(max(1, n))

    out: list[CaseRef] = []
    for _, row in chosen.iterrows():
        d = row["fecha"]
        if isinstance(d, dt.datetime):
            d = d.date()
        elif not isinstance(d, dt.date):
            d = dt.date.fromisoformat(str(d)[:10])
        out.append(CaseRef(date=d, ruta=str(row["Ruta"])))
    return out


def run_multi(
    algo: str,
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
) -> MultiRunStats:
    """Run `algo` over many (date, ruta) cases and return aggregated stats."""
    if algo not in REGISTRY:
        raise ValueError(f"Unknown algorithm: {algo}")

    if builder is None or clients is None or network is None or sim is None:
        raw = load_all()
        catalog = Catalog.build(raw)
        clients = clients or Clients.build(raw)
        builder = builder or DayCaseBuilder(raw, catalog, clients)
        network = network or Network()
        sim = sim or Simulator(clients=clients, network=network)

    case_refs = _sample_cases(
        builder,
        cases=cases,
        sample=sample,
        n=n,
        min_clients=min_clients,
        seed=seed,
    )

    case_results: list[CaseResult] = []
    failures: list[dict] = []
    kpi_buckets: dict[str, list[float]] = {k: [] for k in _AGG_KEYS}
    n_invalid = 0
    n_with_physics = 0
    total_physics = 0
    total_val_errors = 0
    total_val_warnings = 0
    total_drops = 0
    total_cap_violations = 0

    t0_outer = time.perf_counter()
    algo_obj = get(algo)

    for ref in case_refs:
        t0 = time.perf_counter()
        try:
            case = builder.build(ref.date, ref.ruta, truck_code=truck_code)
        except Exception as exc:
            elapsed = time.perf_counter() - t0
            err = f"build: {exc!r}"
            case_results.append(CaseResult(
                date=str(ref.date), ruta=ref.ruta,
                success=False, error=err, fit_ok=False,
                validation_errors=0, validation_warnings=0,
                physics_violations=0, kpis=None, elapsed_sec=elapsed,
            ))
            failures.append({"date": str(ref.date), "ruta": ref.ruta, "error": err})
            continue

        try:
            fit = builder.fit_check(case.orders, case.truck)
            fit_ok = bool(getattr(fit, "fits", True))
        except Exception:
            fit_ok = True

        try:
            plan = algo_obj.plan(case, clients, network)
            result = sim.run(case, plan)
            kpi = compute(result)
            validation = validate_plan(case, plan, result, sim)
        except Exception as exc:
            elapsed = time.perf_counter() - t0
            err = f"run: {exc!r}"
            case_results.append(CaseResult(
                date=str(ref.date), ruta=ref.ruta,
                success=False, error=err, fit_ok=fit_ok,
                validation_errors=0, validation_warnings=0,
                physics_violations=0, kpis=None, elapsed_sec=elapsed,
            ))
            failures.append({"date": str(ref.date), "ruta": ref.ruta, "error": err})
            continue

        elapsed = time.perf_counter() - t0
        physics = sum(
            1 for e in result.log.events if e.kind == "PHYSICS_VIOLATION"
        )
        kpi_dict = kpi.to_dict()
        for k in _AGG_KEYS:
            v = kpi_dict.get(k)
            if isinstance(v, (int, float)) and v == v:  # not NaN
                kpi_buckets[k].append(float(v))

        v_errors = validation.errors
        v_warnings = validation.warnings
        if result.success and v_errors > 0:
            n_invalid += 1
        if result.success and physics > 0:
            n_with_physics += 1
        total_physics += physics
        total_val_errors += v_errors
        total_val_warnings += v_warnings
        total_drops += int(kpi_dict.get("drops") or 0)
        total_cap_violations += int(kpi_dict.get("capacity_violations") or 0)

        # Compact issue list — keep only the validator's structured info
        # (severity / code / message / where), drop the heavy `detail` blob
        # so the API payload stays small even on big sweeps.
        issues_compact = [
            {
                "severity": (
                    i.severity.value if hasattr(i.severity, "value") else str(i.severity)
                ),
                "code": i.code,
                "message": i.message,
                "where": i.where,
            }
            for i in validation.issues
        ]
        case_results.append(CaseResult(
            date=str(ref.date), ruta=ref.ruta,
            success=bool(result.success), error=result.error,
            fit_ok=fit_ok,
            validation_errors=v_errors,
            validation_warnings=v_warnings,
            physics_violations=physics,
            kpis=_compact_kpis(kpi_dict),
            elapsed_sec=elapsed,
            issues=issues_compact,
        ))
        if not result.success:
            failures.append({
                "date": str(ref.date), "ruta": ref.ruta,
                "error": result.error or "sim failed",
            })

    aggregates = {k: _summarize(kpi_buckets[k]) for k in _AGG_KEYS}
    aggregates["wall_clock_sec"] = _summarize(
        [c.elapsed_sec for c in case_results if c.elapsed_sec > 0]
    )

    n_success = sum(1 for c in case_results if c.success)
    stats = MultiRunStats(
        algorithm=algo,
        n_cases=len(case_results),
        n_success=n_success,
        n_failed=len(case_results) - n_success,
        n_invalid_plan=n_invalid,
        n_with_physics=n_with_physics,
        total_physics_violations=total_physics,
        total_validation_errors=total_val_errors,
        total_validation_warnings=total_val_warnings,
        total_drops=total_drops,
        total_capacity_violations=total_cap_violations,
        aggregates=aggregates,
        cases=case_results,
        failures=failures,
        duration_sec=time.perf_counter() - t0_outer,
    )
    return stats


def _compact_kpis(kpi: dict) -> dict:
    """Pick a small subset for per-case display — full payload would
    bloat the response when running on many cases."""
    keep = (
        "total_minutes", "drive_minutes", "service_minutes",
        "total_km", "search_moves", "total_cost_eur",
        "fill_rate", "pallets_loaded", "drops",
        "capacity_violations", "delivered_units", "ordered_units",
        "placement_rejections", "lost_units",
    )
    return {k: kpi.get(k) for k in keep}


def write_csv(stats: MultiRunStats, prefix: str = "multi_run") -> dict[str, str]:
    """Dump per-case + summary CSVs into REPORTS_DIR. Returns paths."""
    import csv

    cases_path = REPORTS_DIR / f"{prefix}_{stats.algorithm}_cases.csv"
    summ_path = REPORTS_DIR / f"{prefix}_{stats.algorithm}_summary.csv"

    case_rows = [c.to_dict() for c in stats.cases]
    fieldnames: list[str] = []
    for r in case_rows:
        for k in r:
            if k not in fieldnames:
                fieldnames.append(k)
        if r.get("kpis"):
            for k in r["kpis"]:
                col = f"kpi_{k}"
                if col not in fieldnames:
                    fieldnames.append(col)

    with cases_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in case_rows:
            row = dict(r)
            for k, v in (r.get("kpis") or {}).items():
                row[f"kpi_{k}"] = v
            row.pop("kpis", None)
            w.writerow(row)

    with summ_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["metric", "n", "sum", "mean", "median", "stdev", "min", "max", "p95"])
        for metric, agg in stats.aggregates.items():
            w.writerow([
                metric, agg["n"], agg["sum"], agg["mean"], agg["median"],
                agg["stdev"], agg["min"], agg["max"], agg["p95"],
            ])
    return {"cases_csv": str(cases_path), "summary_csv": str(summ_path)}
