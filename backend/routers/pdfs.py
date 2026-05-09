from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from backend.pdf.hoja_carga import generate_hoja_carga
from backend.pdf.hoja_ruta import generate_hoja_ruta
from backend.pdf.albaran import generate_albaran

router = APIRouter(prefix="/pdf", tags=["pdf"])


def _load_case(request: Request, date: str, ruta: str):
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
    return case, clients_map, dl


def _pdf_response(pdf_bytes: bytes | bytearray, filename: str) -> Response:
    return Response(
        content=bytes(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@router.get("/hoja-carga/{date}/{ruta}")
def get_hoja_carga(date: str, ruta: str, request: Request):
    case, clients_map, dl = _load_case(request, date, ruta)
    pdf_bytes = generate_hoja_carga(case, clients_map, dl.catalog)
    return _pdf_response(pdf_bytes, f"hoja_carga_{date}_{ruta}.pdf")


@router.get("/hoja-ruta/{date}/{ruta}")
def get_hoja_ruta(date: str, ruta: str, request: Request):
    case, clients_map, dl = _load_case(request, date, ruta)
    pdf_bytes = generate_hoja_ruta(case, clients_map)
    return _pdf_response(pdf_bytes, f"hoja_ruta_{date}_{ruta}.pdf")


@router.get("/albaran/{date}/{ruta}/{client_id}")
def get_albaran(date: str, ruta: str, client_id: str, request: Request):
    case, clients_map, dl = _load_case(request, date, ruta)
    order = next((o for o in case.orders if o.client_id == client_id), None)
    if order is None:
        raise HTTPException(404, f"Client {client_id} not in route {ruta} on {date}")
    client = clients_map.get(client_id)
    pdf_bytes = generate_albaran(case, order, client, dl.catalog)
    return _pdf_response(pdf_bytes, f"albaran_{date}_{ruta}_{client_id}.pdf")
