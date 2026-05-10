"""Pluggable algorithms producing Plans.

Three algorithms shipped:
  - `historic`       — driver's actual visit order + COG-aware client-block
                        loading (the production algorithm).
  - `historic-load`  — cheapest possible warehouse load (pure SKU-block,
                        load-by-reference) + TSP-optimal driver route.
                        Used to compare loader savings vs driver cost.
  - `ev-route`       — greedy expected-cost route (drive_now + NN-horizon
                        + volume penalty) on top of HistoricMimic loading.
"""

from simulator.algorithms.base import Algorithm
from simulator.algorithms.ev_route import EVRoute
from simulator.algorithms.historic import HistoricMimic
from simulator.algorithms.historic_load import HistoricLoad


REGISTRY: dict[str, type[Algorithm]] = {
    "historic": HistoricMimic,
    "historic-load": HistoricLoad,
    "ev-route": EVRoute,
}


def get(name: str) -> Algorithm:
    cls = REGISTRY.get(name)
    if cls is None:
        raise KeyError(f"Unknown algorithm: {name}. Known: {list(REGISTRY)}")
    return cls()
