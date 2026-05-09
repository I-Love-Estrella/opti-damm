# Damm Smart Truck — Simulator Plan

## Goal

A Python simulator that:
1. Loads all provided Damm data (Hackaton.xlsx, ZM040.XLSX, Horarios Entrega.XLSX, Layout Mollet.xlsx).
2. Reconstructs any historical delivery day as a reproducible test case.
3. Accepts an algorithm that produces a Plan (load layout + route + per-stop actions).
4. Executes the Plan as a sequence of commands and computes KPIs (time, cost, ergonomics, service quality, returnables, constraint compliance).
5. Runs algorithms in batch over all available historical days for fair comparison.

## Architecture

```
                            ┌─────────────────────┐
   raw Excel ───► Loader ──►│ DataFrames (cached) │
                            └─────────────────────┘
                                       │
                                       ▼
            (FECHA, Ruta) ──► DayCaseBuilder ──► DayCase
                                                  │
                                                  ▼
                                            Algorithm ──► Plan
                                                                │
                                                                ▼
                                       Simulator ──► Result
                                                       │
                                                       ▼
                                              KPI + EventLog
```

`Algorithm` is an interface — swap to compare strategies. `Simulator` is fixed physics.

## Module layout

```
simulator/
├── config.py                    # paths, tariffs, defaults
├── data/
│   ├── loader.py                # Excel → DataFrames + parquet cache
│   ├── catalog.py               # SKU master + dimension fallbacks
│   ├── clients.py               # client master + time windows
│   ├── orders.py                # day extraction (FECHA, Ruta) → DayCase
│   ├── geocode.py               # CP → coordinates (deterministic)
│   └── network.py               # haversine-based distance/time
├── domain/
│   ├── truck.py                 # truck specs, slots
│   ├── pallet.py                # cargo model (pallet, slot, items)
│   ├── commands.py              # PICK / LOAD / DRIVE_TO / UNLOAD / …
│   └── plan.py                  # Plan = ordered Commands
├── core/
│   ├── state.py                 # WorldState (time, location, cargo)
│   ├── events.py                # event log
│   └── simulator.py             # executes Plan, computes physics
├── kpis/
│   ├── metrics.py               # all KPIs in one dataclass
│   └── aggregate.py             # multi-day stats
├── algorithms/
│   ├── base.py                  # Algorithm ABC
│   ├── replay.py                # baseline: replay actual driver
│   └── nearest.py               # NN route + client-block loading
├── bench/
│   └── runner.py                # run N (FECHA, Ruta) cases × algorithms
└── cli.py                       # python -m simulator.cli ...
```

## Phases

| # | Phase | Output |
|---|---|---|
| 1 | Skeleton + config | repo skeleton, requirements |
| 2 | Data layer | All Excel loaded once → parquet cache. Day extraction works. |
| 3 | Domain models | Truck, Pallet, Slot, Plan, Command |
| 4 | Simulator core | Executes commands, accumulates time/km/cost/search_moves |
| 5 | KPIs | Per-day metric struct + multi-day aggregator |
| 6 | Algorithms | `replay` (baseline) + `nearest` (smart v1) |
| 7 | Benchmark runner | Iterate days × algorithms → CSV table |
| 8 | CLI + smoke test | `python -m simulator.cli ...` works end-to-end |

## Key design decisions

- **Synthetic but deterministic geocoordinates** from postal code hash (anchored on Mollet). No internet needed; same CP → same point. Real geocoding is a future swap.
- **Pallet-level model**, not box-level. EU pallet = 1.2×0.8×1.8 m.
- **Truck types** from the Mollet brief: T6 (×11), T8 (×4), V3 (×1).
- **search_moves** = number of obstructing items the driver has to displace at each UNLOAD; this is the killer KPI vs. "load by reference".
- **Lateral access**: each truck has L (left tarp), R (right tarp) sides; pallets accessible via their side.
- **Time model**: drive time from network matrix, service time = base + per-pallet, plus search-move penalty.
- **Cost model**: fuel + labor (with overtime) + CO₂ + vehicle wear.
- **Reverse logistics**: each non-empty UNLOAD potentially generates an empty PICKUP_RETURN; algorithm decides where to put it.

## Data caching

On first run: read all Excel → write parquet under `data_cache/`. Subsequent runs: read parquet (~100x faster).

## Test strategy

- Smoke test: pick first available (FECHA, Ruta), run `replay` → assert no exceptions, KPIs are non-zero.
- Bench: run both algorithms over 30 random historical days → produce comparison CSV.

## Out of scope (this 24h)

- Real geocoding via Nominatim/Google.
- 3D bin-packing inside pallets.
- True OR-Tools VRPTW (will add later as `algorithms/ortools_vrp.py`).
- Web UI / interactive dashboard (later).

