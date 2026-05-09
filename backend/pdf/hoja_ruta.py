from __future__ import annotations

import datetime as dt

from fpdf import FPDF

from simulator.data.clients import ClientRecord
from simulator.data.orders import DayCase


def _fmt_date(d) -> str:
    if hasattr(d, "strftime"):
        return d.strftime("%d.%m.%Y")
    parts = str(d).split("-")
    return f"{parts[2]}.{parts[1]}.{parts[0]}"


def _fmt_num(val: float, decimals: int = 2) -> str:
    formatted = f"{val:,.{decimals}f}"
    return formatted.replace(",", "X").replace(".", ",").replace("X", ".")


def generate_hoja_ruta(
    case: DayCase, clients: dict[str, ClientRecord]
) -> bytes:
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)

    _page1(pdf, case, clients)
    _page2(pdf, case, clients)

    return pdf.output()


def _top_right(pdf: FPDF, page: int) -> None:
    now = dt.datetime.now()
    pdf.set_font("Helvetica", "", 8)
    pdf.cell(95, 4, "Movi", border=0)
    pdf.cell(95, 4, "", border=0, align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(95, 4, "", border=0)
    pdf.cell(95, 4, now.strftime("%H:%M:%S"), border=0, align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(95, 4, "", border=0)
    pdf.cell(95, 4, f"Página {page}", border=0, align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(95, 4, "", border=0)
    pdf.cell(95, 4, "DDIDGP", border=0, align="R", new_x="LMARGIN", new_y="NEXT")


def _meta_header(pdf: FPDF, case: DayCase) -> None:
    transport = ", ".join(case.raw_transports) or "-"
    labels = ["Nº Carga", "Fecha de entrega", "Vehículo", "Repartidor /", "Nombre", "Preparador", "Nº viaje"]
    values = [transport, _fmt_date(case.date), case.truck.name, case.repartidor, "", "", "01"]
    col_w = [26, 28, 22, 24, 30, 26, 16]

    pdf.set_font("Helvetica", "B", 7)
    for i, lbl in enumerate(labels):
        pdf.cell(col_w[i], 5, lbl, border="B")
    pdf.ln()
    pdf.set_font("Helvetica", "", 7)
    for i, val in enumerate(values):
        pdf.cell(col_w[i], 5, val, border=0)
    pdf.ln(8)


def _page1(pdf: FPDF, case: DayCase, clients: dict[str, ClientRecord]) -> None:
    pdf.add_page()
    _top_right(pdf, 1)
    pdf.ln(2)

    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, "RELACIÓN DE DOCUMENTOS DE LA CARGA POR FORMA DE PAGO",
             new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(4)

    _meta_header(pdf, case)

    col_w = [10, 24, 16, 22, 30, 34, 28, 26]
    headers = ["SSTT", "Condición de pago", "Nº Doc.", "Nº Cliente", "Nombre 2", "Dirección",
               "Total Proforma", "Total Cobro"]

    pdf.set_font("Helvetica", "B", 7)
    y_hdr = pdf.get_y()
    x_hdr = pdf.get_x()
    for i, h in enumerate(headers):
        pdf.cell(col_w[i], 10, h, border=1)
    pdf.ln()
    pdf.set_font("Helvetica", "", 6)
    x_sub = x_hdr + sum(col_w[:6])
    pdf.set_xy(x_sub, y_hdr + 4)
    pdf.cell(col_w[6], 5, "(IVA Incl.)", border=0)
    pdf.set_y(y_hdr + 10)

    pdf.set_font("Helvetica", "", 7)
    total_carga = 0.0
    total_cobro = 0.0
    n_pedidos = 0

    for order in case.orders:
        c = clients.get(order.client_id)
        name = (c.name if c else order.client_id)[:20]
        address = ""
        if c:
            address = f"{c.address} {c.city}"[:26]

        weight_approx = order.total_weight_kg
        total_carga += weight_approx
        n_pedidos += 1

        row_h = 6
        if len(address) > 22:
            row_h = 10

        pdf.cell(col_w[0], row_h, "NO", border=1)
        pdf.cell(col_w[1], row_h, "CREDITO", border=1)
        pdf.cell(col_w[2], row_h, "", border=1)
        pdf.cell(col_w[3], row_h, order.client_id[:14], border=1)
        pdf.cell(col_w[4], row_h, name, border=1)
        pdf.cell(col_w[5], row_h, address, border=1)
        pdf.cell(col_w[6], row_h, _fmt_num(weight_approx), border=1, align="R")
        pdf.cell(col_w[7], row_h, "0,00", border=1, align="R")
        pdf.ln()

        if pdf.get_y() > 265:
            pdf.add_page()
            _top_right(pdf, 1)
            _meta_header(pdf, case)

    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 8)
    pdf.cell(sum(col_w[:4]), 6, "Nº de pedidos", border=1, align="C")
    pdf.cell(col_w[4], 6, str(n_pedidos), border=1, align="C")
    pdf.cell(col_w[5], 6, "T. Carga", border=1, align="C")
    pdf.cell(col_w[6], 6, _fmt_num(total_carga), border=1, align="R")
    pdf.cell(col_w[7], 6, _fmt_num(total_cobro), border=1, align="R")
    pdf.ln()


def _page2(pdf: FPDF, case: DayCase, clients: dict[str, ClientRecord]) -> None:
    pdf.add_page()
    _top_right(pdf, 2)
    pdf.ln(2)

    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, "RELACIÓN DE DOCUMENTOS DE LA CARGA POR FORMA DE PAGO",
             new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(4)

    _meta_header(pdf, case)

    col_w2 = [30, 40, 20, 40]
    headers2 = ["SSTT", "Condición de pago", "Docs.", "Imp. (IVA Incl.)"]
    table_w = sum(col_w2)
    x_offset = pdf.l_margin + (190 - table_w) / 2

    pdf.set_font("Helvetica", "B", 8)
    pdf.set_x(x_offset)
    for i, h in enumerate(headers2):
        pdf.cell(col_w2[i], 6, h, border=1)
    pdf.ln()

    pdf.set_font("Helvetica", "", 8)
    n = len(case.orders)
    pdf.set_x(x_offset)
    pdf.cell(col_w2[0], 6, "NO", border=1)
    pdf.cell(col_w2[1], 6, "CREDITO", border=1)
    pdf.cell(col_w2[2], 6, str(n), border=1, align="C")
    pdf.cell(col_w2[3], 6, "0,00", border=1, align="R")
    pdf.ln()

    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_x(x_offset)
    pdf.cell(col_w2[0], 6, "", border=1)
    pdf.cell(col_w2[1], 6, "Total Contado", border=1)
    pdf.cell(col_w2[2], 6, "0", border=1, align="C")
    pdf.cell(col_w2[3], 6, "0,00", border=1, align="R")
    pdf.ln()
