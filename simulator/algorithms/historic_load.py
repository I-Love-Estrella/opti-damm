"""Historic-load — warehouse-cheap loading + TSP-optimal driver route.

Premise (the user's request):
  1. Pack the warehouse load **as cheaply and quickly as possible** —
     pure load-by-reference (one SKU at a time, picked from its zone,
     dumped onto whatever pallet has room). No client awareness, no
     delivery-order bookkeeping, no LIFO trickery. The loader does the
     simplest possible job, takes the least time, and gets paid the
     loader rate (€12/h).
  2. **Then** route the driver around that fixed load with a TSP-style
     shortest-path solver (NN + 2-opt + or-opt), so the truck still
     drives the most efficient kilometres given what was loaded.

Trade-off this exposes:
  - Depot time falls (fewer Picks per pallet, fewer pallet builds).
  - Loader cost falls accordingly: depot_minutes × loader_hourly_eur.
  - Driver time RISES — at every stop the items for that client are
    scattered across multiple SKU-block pallets; he digs through other
    clients' stuff to reach his (search_moves go up dramatically).
  - Driver cost rises: driver_minutes × driver_hourly_eur (overtime
    over 8 h). Comparing labor_eur of `historic` vs `historic-load`
    tells the dispatcher whether the warehouse savings outpace the
    driver overtime — and at what driver hourly rate the equation
    flips. See `docs/ALGORITHMS.md` (or run `python3 scripts/break_even.py`)
    for the per-day numbers.

Inheritance:
  - We override `_chunk_sort_key` to ignore `delivery_seq` (no client-
    block sub-ordering) and sort purely by SKU.
  - We override `_delivery_route` to run NN + 2-opt + or-opt on the
    final on-road sequence.
  - Everything else (per-class slot quota, returnables strategy,
    overflow handling, _column_ok) is inherited from HistoricMimic.
"""

from __future__ import annotations

from simulator.algorithms.historic import HistoricMimic, _Chunk
from simulator.data.clients import Clients
from simulator.data.network import Network
from simulator.data.orders import ClientOrder, DayCase


_NN_2OPT_PASSES = 2
_OR_OPT_PASSES = 2


class HistoricLoad(HistoricMimic):
    name = "historic-load"
    description = (
        "Cheapest possible warehouse load — pure SKU-block (load-by-"
        "reference), no client awareness. Driver gets a TSP-optimal "
        "route on top but pays for it with extra search-moves at "
        "delivery. Splits labor: loader saves time at €12/h, driver "
        "spends more at €18/h."
    )

    # ---- Loading: pure SKU-block (no client awareness) ----------------

    def _chunk_sort_key(
        self,
        c: _Chunk,
        sku_max_weight: dict[str, float],
        sku_total_volume: dict[str, float],
        delivery_seq: dict[str, int],
    ) -> tuple:
        """Pure load-by-reference: heaviest SKU first (so it lands at
        the floor — CRUSH safety), then by total SKU volume, then SKU
        id alphabetically. The CLIENT identity is the lowest-priority
        tiebreaker — chunks of the same SKU stay together regardless of
        which customer gets them. The loader never has to think about
        the route order.

        delivery_seq is intentionally NOT in the key.
        """

        return (
            -sku_max_weight[c.sku],     # heavy SKU at the bottom
            -sku_total_volume[c.sku],   # bigger SKU first
            c.sku,                      # alphabetical for stability
            c.client_id,                # tiebreaker only
        )

    # ---- Routing: TSP-shortest on top of the fixed load --------------

    def _delivery_route(
        self,
        case: DayCase,
        clients: Clients,
        network: Network,
        loading_route: list[ClientOrder],
    ) -> list[ClientOrder]:
        """NN seed → 2-opt → or-opt over the loading route's clients.

        We re-optimize the on-road sequence even though the warehouse
        already laid out the truck — the truck's content is fixed, only
        the driving order shifts. Search-moves at each stop are higher
        than `historic` (load is SKU-block, not LIFO-clean), but we
        save kilometres and per-kilometre fuel / wear / time.
        """

        seq = self._nearest_neighbor(loading_route, case, clients, network)
        seq = self._two_opt(seq, case, clients, network, max_passes=_NN_2OPT_PASSES)
        seq = self._or_opt(seq, case, clients, network, max_passes=_OR_OPT_PASSES)
        return seq

    # ---- Routing helpers (kept local so this file is self-contained) -

    @staticmethod
    def _route_km(
        seq: list[ClientOrder],
        case: DayCase,
        clients: Clients,
        network: Network,
    ) -> float:
        loc = (case.depot.lat, case.depot.lon)
        total = 0.0
        for o in seq:
            c = clients.get(o.client_id)
            total += network.leg(loc, (c.lat, c.lon)).distance_km
            loc = (c.lat, c.lon)
        total += network.leg(loc, (case.depot.lat, case.depot.lon)).distance_km
        return total

    @staticmethod
    def _nearest_neighbor(
        orders: list[ClientOrder],
        case: DayCase,
        clients: Clients,
        network: Network,
    ) -> list[ClientOrder]:
        remaining = list(orders)
        loc = (case.depot.lat, case.depot.lon)
        ordered: list[ClientOrder] = []
        while remaining:
            best = min(
                remaining,
                key=lambda o: network.leg(
                    loc, (clients.get(o.client_id).lat, clients.get(o.client_id).lon)
                ).distance_km,
            )
            ordered.append(best)
            remaining.remove(best)
            c = clients.get(best.client_id)
            loc = (c.lat, c.lon)
        return ordered

    def _two_opt(
        self,
        order: list[ClientOrder],
        case: DayCase,
        clients: Clients,
        network: Network,
        max_passes: int,
    ) -> list[ClientOrder]:
        best = order
        best_km = self._route_km(best, case, clients, network)
        for _ in range(max_passes):
            improved = False
            for i in range(len(best) - 1):
                for j in range(i + 1, len(best)):
                    cand = best[:i] + list(reversed(best[i:j + 1])) + best[j + 1:]
                    km = self._route_km(cand, case, clients, network)
                    if km + 1e-6 < best_km:
                        best, best_km, improved = cand, km, True
                        break
                if improved:
                    break
            if not improved:
                break
        return best

    def _or_opt(
        self,
        order: list[ClientOrder],
        case: DayCase,
        clients: Clients,
        network: Network,
        max_passes: int,
    ) -> list[ClientOrder]:
        best = order
        best_km = self._route_km(best, case, clients, network)
        for _ in range(max_passes):
            improved = False
            n = len(best)
            for size in (1, 2):
                if improved:
                    break
                for i in range(n - size + 1):
                    if improved:
                        break
                    seg = best[i : i + size]
                    rest = best[:i] + best[i + size :]
                    for j in range(len(rest) + 1):
                        if j == i:
                            continue
                        cand = rest[:j] + seg + rest[j:]
                        if cand == best:
                            continue
                        km = self._route_km(cand, case, clients, network)
                        if km + 1e-6 < best_km:
                            best, best_km, improved = cand, km, True
                            break
            if not improved:
                break
        return best
