from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, HTTPException, Request

from backend.schemas import (
    ClientOrderOut,
    DayCaseOut,
    OrderLineOut,
    RouteSummary,
    TruckOut,
)

router = APIRouter(prefix="/routes", tags=["routes"])


@router.get("", response_model=list[RouteSummary])
def list_routes(request: Request):
    dl = request.app.state.dl
    df = dl.list_day_cases()
    return [
        RouteSummary(
            fecha=str(row["fecha"]),
            ruta=str(row["Ruta"]),
            clients=int(row["clients"]),
            lines=int(row["lines"]),
        )
        for _, row in df.iterrows()
    ]


@router.get("/{date}/{ruta}", response_model=DayCaseOut)
def get_route(date: str, ruta: str, request: Request):
    dl = request.app.state.dl
    try:
        fecha = dt.date.fromisoformat(date)
    except ValueError:
        raise HTTPException(400, f"Invalid date: {date}")
    try:
        case = dl.build_case(fecha, ruta)
    except KeyError:
        raise HTTPException(404, f"No data for {date} / {ruta}")

    clients_map = dl.all_clients()
    orders = []
    for o in case.orders:
        c = clients_map.get(o.client_id)
        client_name = c.name if c else o.client_id
        orders.append(
            ClientOrderOut(
                client_id=o.client_id,
                client_name=client_name,
                lines=[
                    OrderLineOut(
                        sku=l.sku,
                        qty=l.qty,
                        uma=l.uma,
                        unit_volume_m3=l.unit_volume_m3,
                        unit_weight_kg=l.unit_weight_kg,
                        is_returnable=l.is_returnable,
                    )
                    for l in o.lines
                ],
                expected_returnable_units=o.expected_returnable_units,
                visit_seq=o.visit_seq_actual,
                total_volume_m3=o.total_volume_m3,
                total_weight_kg=o.total_weight_kg,
            )
        )

    return DayCaseOut(
        date=str(case.date),
        ruta=case.ruta,
        repartidor=case.repartidor,
        truck=TruckOut(
            code=case.truck.code,
            name=case.truck.name,
            pallet_capacity=case.truck.pallet_capacity,
            max_weight_kg=case.truck.max_weight_kg,
        ),
        transports=list(case.raw_transports),
        n_clients=case.n_clients,
        total_volume_m3=case.total_volume_m3,
        orders=orders,
    )
