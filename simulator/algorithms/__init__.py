"""Pluggable algorithms producing Plans."""

from simulator.algorithms.balanced import BalancedLoader
from simulator.algorithms.base import Algorithm
from simulator.algorithms.historic import HistoricMimic
from simulator.algorithms.lifo import LifoArchitect
from simulator.algorithms.nearest import NearestNeighborSmart
from simulator.algorithms.replay import ReplayBaseline


REGISTRY: dict[str, type[Algorithm]] = {
    "replay": ReplayBaseline,
    "nearest": NearestNeighborSmart,
    "balanced": BalancedLoader,
    "lifo": LifoArchitect,
    "historic": HistoricMimic,
}


def get(name: str) -> Algorithm:
    cls = REGISTRY.get(name)
    if cls is None:
        raise KeyError(f"Unknown algorithm: {name}. Known: {list(REGISTRY)}")
    return cls()
