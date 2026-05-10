"""Plan = ordered command sequence + lightweight metadata for explainability."""

from __future__ import annotations

from dataclasses import dataclass, field

from simulator.domain.commands import Command


@dataclass(frozen=True)
class Plan:
    algorithm: str
    commands: tuple[Command, ...]
    rationale: tuple[str, ...] = field(default_factory=tuple)
    route_order: tuple[str, ...] = field(default_factory=tuple)
    # Chunks the algorithm couldn't pack at planning time. The
    # simulator surfaces these in WorldState.pack_overflow so KPIs
    # can distinguish "algorithm dropped at plan time" (overflow)
    # from "simulator rejected at runtime" (placement_rejections).
    # Each entry: (client_id, sku, qty).
    pack_overflow: tuple[tuple[str, str, float], ...] = field(default_factory=tuple)
