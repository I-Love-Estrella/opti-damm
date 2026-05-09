from __future__ import annotations

import datetime as dt

import pytest

from backend.data_layer import DataLayer


@pytest.fixture(scope="module")
def dl():
    return DataLayer()


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from backend.main import app
    with TestClient(app) as c:
        yield c


# --- Data Layer ---

def test_data_layer_loads(dl):
    cases = dl.list_day_cases()
    assert len(cases) > 0
    assert "fecha" in cases.columns
    assert "Ruta" in cases.columns


def test_build_day_case(dl):
    case = dl.build_case(dt.date(2026, 2, 5), "DR0027")
    assert case.ruta == "DR0027"
    assert case.n_clients >= 10
    assert len(case.orders) > 0


def test_get_client(dl):
    clients = dl.all_clients()
    assert len(clients) > 0
    first_id = next(iter(clients))
    client = dl.get_client(first_id)
    assert client.client_id == first_id
    assert client.name


# --- Routes API ---

def test_list_routes(client):
    resp = client.get("/routes")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) > 0
    assert "fecha" in data[0]
    assert "ruta" in data[0]


def test_get_route_detail(client):
    resp = client.get("/routes/2026-02-05/DR0027")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ruta"] == "DR0027"
    assert data["n_clients"] >= 10
    assert len(data["orders"]) > 0
    first_order = data["orders"][0]
    assert "client_id" in first_order
    assert "lines" in first_order


def test_get_route_not_found(client):
    resp = client.get("/routes/2099-01-01/NOPE")
    assert resp.status_code == 404


# --- Clients API ---

def test_list_clients(client):
    resp = client.get("/clients")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) > 100


def test_get_client_detail(client):
    resp = client.get("/clients")
    first_id = resp.json()[0]["client_id"]
    resp2 = client.get(f"/clients/{first_id}")
    assert resp2.status_code == 200
    assert resp2.json()["client_id"] == first_id


# --- PDF Generation ---

def test_hoja_carga_generates(dl):
    from backend.pdf.hoja_carga import generate_hoja_carga

    case = dl.build_case(dt.date(2026, 2, 5), "DR0027")
    clients_map = dl.all_clients()
    pdf_bytes = generate_hoja_carga(case, clients_map, dl.catalog)
    assert len(pdf_bytes) > 500
    assert pdf_bytes[:5] == b"%PDF-"


def test_hoja_ruta_generates(dl):
    from backend.pdf.hoja_ruta import generate_hoja_ruta

    case = dl.build_case(dt.date(2026, 2, 5), "DR0027")
    clients_map = dl.all_clients()
    pdf_bytes = generate_hoja_ruta(case, clients_map)
    assert len(pdf_bytes) > 500
    assert pdf_bytes[:5] == b"%PDF-"


def test_albaran_generates(dl):
    from backend.pdf.albaran import generate_albaran

    case = dl.build_case(dt.date(2026, 2, 5), "DR0027")
    clients_map = dl.all_clients()
    order = case.orders[0]
    client = clients_map.get(order.client_id)
    pdf_bytes = generate_albaran(case, order, client, dl.catalog)
    assert len(pdf_bytes) > 500
    assert pdf_bytes[:5] == b"%PDF-"


# --- PDF Endpoints ---

def test_pdf_hoja_carga_endpoint(client):
    resp = client.get("/pdf/hoja-carga/2026-02-05/DR0027")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content[:5] == b"%PDF-"


def test_pdf_hoja_ruta_endpoint(client):
    resp = client.get("/pdf/hoja-ruta/2026-02-05/DR0027")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"


def test_pdf_albaran_endpoint(client):
    route_resp = client.get("/routes/2026-02-05/DR0027")
    first_client_id = route_resp.json()["orders"][0]["client_id"]
    resp = client.get(f"/pdf/albaran/2026-02-05/DR0027/{first_client_id}")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"


def test_pdf_albaran_bad_client(client):
    resp = client.get("/pdf/albaran/2026-02-05/DR0027/FAKE_CLIENT")
    assert resp.status_code == 404
