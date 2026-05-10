"""EV-route — greedy delivery route over expected total cost.

At each decision point we pick the next client to visit by minimising:

    score(c) = w_drive  · drive_min(pos → c)
             + w_future · nn_continuation(c, remaining \\ {c})
             + w_volume · volume_penalty(c, n_remaining)

where:
  - **drive_min**  is the immediate leg cost from the truck's current
    location to candidate `c` (Network already returns minutes).
  - **nn_continuation** is a cheap nearest-neighbour rollout over the
    remaining clients starting from `c`'s location — a lower-bound
    estimate of "how much driving is still left after I commit to c".
    Without this term the algorithm collapses to pure NN.
  - **volume_penalty** captures load-side cost: HistoricMimic's loader
    puts late-delivery clients on the floor (LIFO-clean), so visiting
    a high-volume / high-weight client EARLY forces the loader to
    plant a large block at the top — which then needs many lifts at
    every subsequent stop. Big orders → visit late.

The "expected cost in the future" is a tractable greedy approximation:
full enumeration is n! (15-25 stops per day → infeasible). We trade an
exact lookahead for a 1-step + horizon-K nearest-neighbour estimate.

Loading is inherited verbatim from HistoricMimic (client-block, LIFO-
aware). Only the on-road sequence changes — same pallets, smarter
visit order. This isolates "route savings under realistic loading"
from any loader-side change.
"""

from __future__ import annotations

from simulator.algorithms.historic import HistoricMimic
from simulator.data.clients import Clients
from simulator.data.network import Network
from simulator.data.orders import ClientOrder, DayCase


# --- Cost weights ---------------------------------------------------------
# All weights are in "minute-equivalents" so the score reads as a single
# time budget. Tuned defaults — feel free to expose via constructor.
W_DRIVE = 1.0           # 1 min of immediate driving = 1 unit of score
W_FUTURE = 1.0          # 1 min of estimated remaining driving = 1 unit
W_VOLUME = 8.0          # minutes of penalty per (vol_share × remaining_stops)
LOOKAHEAD_K = 4         # NN rollout horizon (cap to keep per-decision O(n²·k))


class EVRoute(HistoricMimic):
    name = "ev-route"
    description = (
        "Greedy delivery route minimising expected total cost: drive "
        "minutes now + nearest-neighbour estimate of remaining drive + "
        "volume penalty that pushes big orders late so HistoricMimic's "
        "loader places them on the floor (fewer search-moves at every "
        "subsequent stop). Loading inherited from historic."
    )

    # ---- Routing override -------------------------------------------------

    def _delivery_route(
        self,
        case: DayCase,
        clients: Clients,
        network: Network,
        loading_route: list[ClientOrder],
    ) -> list[ClientOrder]:
        """Greedy + 1-step + K-step NN lookahead.

        Note: `loading_route` is the warehouse's assumed visit order
        (HistoricMimic uses `case.orders`). We re-order it for the road
        WITHOUT touching the load — the truck is packed using the
        delivery_seq derived from THIS function's output, then every
        subsequent simulator step (search_moves, blocker lifts, COG)
        respects the chosen sequence.
        """

        if not loading_route:
            return []

        order_vol = {
            o.client_id: float(o.total_volume_m3) for o in loading_route
        }
        max_vol = max(order_vol.values()) or 1.0

        # Pre-cache per-client (lat, lon) — clients.get is O(dict) but
        # we call it n² times in the worst case; one round of caching
        # keeps the inner loop tight.
        loc_by_client: dict[str, tuple[float, float]] = {}
        for o in loading_route:
            c = clients.get(o.client_id)
            loc_by_client[o.client_id] = (c.lat, c.lon)

        depot_loc = (case.depot.lat, case.depot.lon)

        remaining = list(loading_route)
        ordered: list[ClientOrder] = []
        pos = depot_loc

        while remaining:
            best = min(
                remaining,
                key=lambda o: self._score(
                    o,
                    pos,
                    remaining,
                    network,
                    loc_by_client,
                    order_vol,
                    max_vol,
                ),
            )
            ordered.append(best)
            pos = loc_by_client[best.client_id]
            remaining.remove(best)

        return ordered

    # ---- Score components -------------------------------------------------

    @staticmethod
    def _score(
        candidate: ClientOrder,
        pos: tuple[float, float],
        remaining: list[ClientOrder],
        network: Network,
        loc_by_client: dict[str, tuple[float, float]],
        order_vol: dict[str, float],
        max_vol: float,
    ) -> float:
        cand_loc = loc_by_client[candidate.client_id]
        drive_now = network.leg(pos, cand_loc).duration_min

        # Future estimate: K-step nearest-neighbour rollout from candidate
        # over the remaining clients (excluding candidate itself). This is
        # a fast lower-bound proxy — not the optimal continuation, but
        # consistent across candidates so it discriminates fairly.
        rest = [o for o in remaining if o.client_id != candidate.client_id]
        future = EVRoute._nn_horizon(
            cand_loc, rest, network, loc_by_client, k=LOOKAHEAD_K
        )

        # Load-side penalty: HistoricMimic packs LATE-delivery clients
        # on the floor (LIFO-safe). Visiting a heavy / bulky client
        # EARLY forces them on top → many supporters above subsequent
        # clients' items → search_moves explode at every later stop.
        # Penalty zeroes out on the LAST stop (no future disturbance to
        # cause) and grows with both the candidate's relative volume
        # and how many stops are still ahead of it.
        n_after = len(remaining) - 1  # how many stops will follow this one
        vol_share = order_vol[candidate.client_id] / max_vol
        volume_penalty = vol_share * float(n_after)

        return (
            W_DRIVE * drive_now
            + W_FUTURE * future
            + W_VOLUME * volume_penalty
        )

    @staticmethod
    def _nn_horizon(
        start: tuple[float, float],
        rest: list[ClientOrder],
        network: Network,
        loc_by_client: dict[str, tuple[float, float]],
        k: int,
    ) -> float:
        """Sum of the next `k` NN hops starting from `start` over `rest`.

        Returns 0.0 when `rest` is empty (no more driving after this
        stop). Bounded at `min(k, len(rest))` to avoid double-counting
        when fewer clients remain than the horizon.
        """

        if not rest or k <= 0:
            return 0.0
        loc = start
        pool = list(rest)
        total = 0.0
        steps = min(k, len(pool))
        for _ in range(steps):
            best_o = min(
                pool,
                key=lambda o: network.leg(loc, loc_by_client[o.client_id]).duration_min,
            )
            total += network.leg(loc, loc_by_client[best_o.client_id]).duration_min
            loc = loc_by_client[best_o.client_id]
            pool.remove(best_o)
        return total
