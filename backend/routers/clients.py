from __future__ import annotations

from fastapi import APIRouter, Request

from backend.schemas import ClientOut, TimeWindowOut

router = APIRouter(prefix="/clients", tags=["clients"])


@router.get("", response_model=list[ClientOut])
def list_clients(request: Request):
    dl = request.app.state.dl
    clients = dl.all_clients()
    return [_to_out(c) for c in clients.values()]


@router.get("/{client_id}", response_model=ClientOut)
def get_client(client_id: str, request: Request):
    dl = request.app.state.dl
    c = dl.get_client(client_id)
    return _to_out(c)


def _to_out(c) -> ClientOut:
    return ClientOut(
        client_id=c.client_id,
        name=c.name,
        address=c.address,
        cp=c.cp,
        city=c.city,
        lat=c.lat,
        lon=c.lon,
        time_windows=[
            TimeWindowOut(
                weekday=tw.weekday,
                shift=tw.shift,
                start=tw.start.isoformat(),
                end=tw.end.isoformat(),
                closed=tw.closed,
            )
            for tw in c.time_windows
        ],
    )
