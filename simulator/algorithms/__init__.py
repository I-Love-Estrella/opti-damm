"""Pluggable algorithms producing Plans."""

from simulator.algorithms.base import Algorithm
from simulator.algorithms.nearest import NearestNeighborSmart
from simulator.algorithms.replay import ReplayBaseline


REGISTRY: dict[str, type[Algorithm]] = {
    "replay": ReplayBaseline,
    "nearest": NearestNeighborSmart,
}


def get(name: str) -> Algorithm:
    cls = REGISTRY.get(name)
    if cls is None:
        raise KeyError(f"Unknown algorithm: {name}. Known: {list(REGISTRY)}")
    return cls()
