"""Event log emitted as the simulator executes commands."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Event:
    seq: int
    t_min: float
    kind: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class EventLog:
    events: list[Event] = field(default_factory=list)

    def emit(self, t_min: float, kind: str, **detail: Any) -> Event:
        ev = Event(seq=len(self.events), t_min=t_min, kind=kind, detail=dict(detail))
        self.events.append(ev)
        return ev

    def to_records(self) -> list[dict[str, Any]]:
        return [
            {"seq": e.seq, "t_min": e.t_min, "kind": e.kind, **e.detail}
            for e in self.events
        ]
