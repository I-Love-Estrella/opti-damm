"""Algorithm interface."""

from __future__ import annotations

from abc import ABC, abstractmethod

from simulator.data.clients import Clients
from simulator.data.network import Network
from simulator.data.orders import DayCase
from simulator.domain.plan import Plan


class Algorithm(ABC):
    name: str = "abstract"
    description: str = ""

    @abstractmethod
    def plan(self, case: DayCase, clients: Clients, network: Network) -> Plan:
        raise NotImplementedError
