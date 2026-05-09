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
