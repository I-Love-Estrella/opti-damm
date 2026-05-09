from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from fpdf import FPDF

from simulator.data.catalog import Catalog
from simulator.data.clients import ClientRecord
from simulator.data.orders import DayCase

SPANISH_MONTHS = {
    1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril",
    5: "Mayo", 6: "Junio", 7: "Julio", 8: "Agosto",
    9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre",
}


def _fmt_date(d) -> str:
    if hasattr(d, "strftime"):
        return d.strftime("%d.%m.%Y")
    parts = str(d).split("-")
    return f"{parts[2]}.{parts[1]}.{parts[0]}"


def _fmt_num(val: float, decimals: int = 3) -> str:
    formatted = f"{val:,.{decimals}f}"
    return formatted.replace(",", "X").replace(".", ",").replace("X", ".")


def generate_hoja_carga(
    case: DayCase,
    clients: dict[str, ClientRecord],
    catalog: Catalog | None = None,
) -> bytes:
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)

    items = _collect_items(case, catalog)
    envase_prefix = "ENVASE"
    located = [it for it in items if it.ubicacion and it.ubicacion != envase_prefix]
    unlocated = [it for it in items if not it.ubicacion]
    envases = [it for it in items if it.ubicacion == envase_prefix]

    located.sort(key=lambda it: it.ubicacion)
    envases.sort(key=lambda it: it.sku)

    page_num = [0]

    def add_page():
        pdf.add_page()
        page_num[0] += 1
        _page_header(pdf, case, page_num[0])

    add_page()

    if located:
        _section_title(pdf, "Carga lleno")
        _cargo_table(pdf, located, add_page, case, page_num)

        pdf.set_font("Helvetica", "B", 8)
        pdf.cell(0, 6, f"Total Cantidad:      {sum(int(it.cantidad) for it in located)}",
                 new_x="LMARGIN", new_y="NEXT", align="R")
        pdf.ln(4)

    if unlocated:
        if pdf.get_y() > 240:
            add_page()
        _section_title(pdf, "Carga lleno sin ubicación")
        _cargo_table(pdf, unlocated, add_page, case, page_num)

        pdf.set_font("Helvetica", "B", 8)
        pdf.cell(0, 6, f"Total Cantidad:      {sum(int(it.cantidad) for it in unlocated)}",
                 new_x="LMARGIN", new_y="NEXT", align="R")
        pdf.ln(4)

    if envases:
        if pdf.get_y() > 240:
            add_page()
        _section_title(pdf, "Carga envases")
        _envase_table(pdf, envases, add_page, case, page_num)

        pdf.set_font("Helvetica", "B", 8)
        pdf.cell(0, 6, f"Total Cantidad:      {sum(int(it.cantidad) for it in envases)}",
                 new_x="LMARGIN", new_y="NEXT", align="R")
        pdf.ln(4)

    _summary(pdf, case)

    return pdf.output()


@dataclass
class _CargoItem:
    ubicacion: str
    sku: str
    descripcion: str
    cantidad: float
    unidad: str
    lote: str
    descarga: str


def _collect_items(case: DayCase, catalog: Catalog | None) -> list[_CargoItem]:
    uma_map = {
        "CAJ": "Caja", "BRL": "Barril", "UN": "Unidad",
        "BOT": "Botella", "ZPR": "Caja", "PAK": "Pack",
        "CAM": "Caja", "KG": "Kg", "BOX": "Caja",
    }
    agg: dict[tuple[str, str], _CargoItem] = {}
    for order in case.orders:
        for line in order.lines:
            desc = ""
            loc = ""
            if catalog and line.sku in catalog:
                rec = catalog.get(line.sku)
                desc = rec.name[:40]
                loc = rec.warehouse_location or ""
            key = (loc, line.sku)
            if key in agg:
                agg[key].cantidad += line.qty
            else:
                agg[key] = _CargoItem(
                    ubicacion=loc,
                    sku=line.sku,
                    descripcion=desc,
                    cantidad=line.qty,
                    unidad=uma_map.get(line.uma, line.uma),
                    lote="",
                    descarga="",
                )
    return list(agg.values())


def _page_header(pdf: FPDF, case: DayCase, page: int) -> None:
    now = dt.datetime.now()
    pdf.set_font("Helvetica", "", 8)
    pdf.cell(95, 4, "Movi", border=0)
    right_text = f"{now.day:02d} {SPANISH_MONTHS[now.month]} {now.year}"
    pdf.cell(95, 4, right_text, border=0, align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(95, 4, "", border=0)
    pdf.cell(95, 4, now.strftime("%H:%M:%S"), border=0, align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(95, 4, "", border=0)
    pdf.cell(95, 4, f"Página {page}", border=0, align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(95, 4, "", border=0)
    pdf.cell(95, 4, "DDIDGP", border=0, align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    transport = ", ".join(case.raw_transports) or "-"
    col_w = [30, 30, 22, 50, 16, 22, 20]
    labels = ["Nº Carga/Nº precarga", "Vehículo", "Repartidor / Proveedor", "", "Nº Viaje", "Fecha Envío", "Ruta"]
    values = [transport, case.truck.name, case.repartidor, "", "01", _fmt_date(case.date), case.ruta]

    pdf.set_font("Helvetica", "B", 7)
    for i, lbl in enumerate(labels):
        if lbl:
            pdf.cell(col_w[i], 5, lbl, border="B")
        else:
            pdf.cell(col_w[i], 5, "", border=0)
    pdf.ln()
    pdf.set_font("Helvetica", "", 7)
    for i, val in enumerate(values):
        pdf.cell(col_w[i], 5, val, border=0)
    pdf.ln(6)


def _section_title(pdf: FPDF, title: str) -> None:
    pdf.set_font("Helvetica", "B", 8)
    pdf.cell(0, 6, title, new_x="LMARGIN", new_y="NEXT")


def _cargo_table_header(pdf: FPDF) -> None:
    col_w = [22, 22, 68, 18, 18, 18, 24]
    headers = ["Ubicación", "Nº Prod.", "Descripción", "Cantidad", "Unidad", "Lote", "Descarga"]
    pdf.set_font("Helvetica", "B", 7)
    for i, h in enumerate(headers):
        pdf.cell(col_w[i], 5, h, border=1)
    pdf.ln()


def _cargo_table(pdf: FPDF, items: list[_CargoItem], add_page, case: DayCase, page_num: list[int]) -> None:
    col_w = [22, 22, 68, 18, 18, 18, 24]
    _cargo_table_header(pdf)

    pdf.set_font("Helvetica", "", 7)
    for item in items:
        if pdf.get_y() > 265:
            add_page()
            _section_title(pdf, "")
            _cargo_table_header(pdf)
            pdf.set_font("Helvetica", "", 7)

        row = [item.ubicacion, item.sku, item.descripcion,
               f"{item.cantidad:.0f}", item.unidad, item.lote, item.descarga]
        max_h = 5
        desc_text = item.descripcion
        if len(desc_text) > 38:
            max_h = 9

        for i, val in enumerate(row):
            pdf.cell(col_w[i], max_h, val, border=1)
        pdf.ln()


def _envase_table_header(pdf: FPDF) -> None:
    col_w = [22, 22, 68, 18, 18, 42]
    headers = ["Ubicación", "Nº Prod.", "Descripción", "Cantidad", "Unidad", "Descarga"]
    pdf.set_font("Helvetica", "B", 7)
    for i, h in enumerate(headers):
        pdf.cell(col_w[i], 5, h, border=1)
    pdf.ln()


def _envase_table(pdf: FPDF, items: list[_CargoItem], add_page, case: DayCase, page_num: list[int]) -> None:
    col_w = [22, 22, 68, 18, 18, 42]
    _envase_table_header(pdf)

    pdf.set_font("Helvetica", "", 7)
    for item in items:
        if pdf.get_y() > 265:
            add_page()
            _section_title(pdf, "")
            _envase_table_header(pdf)
            pdf.set_font("Helvetica", "", 7)

        row = ["", item.sku, item.descripcion,
               f"{item.cantidad:.0f}", item.unidad, item.descarga]
        max_h = 5
        if len(item.descripcion) > 38:
            max_h = 9

        for i, val in enumerate(row):
            pdf.cell(col_w[i], max_h, val, border=1)
        pdf.ln()


def _summary(pdf: FPDF, case: DayCase) -> None:
    if pdf.get_y() > 240:
        pdf.add_page()

    pdf.ln(6)
    total_qty = sum(int(l.qty) for o in case.orders for l in o.lines)
    total_vol = sum(o.total_volume_m3 for o in case.orders)
    total_weight = sum(o.total_weight_kg for o in case.orders)

    x = pdf.l_margin
    y = pdf.get_y()
    table_w = 190
    half_w = table_w / 2
    label_w = 58
    value_w = half_w - label_w
    row_h = 5

    pdf.set_font("Helvetica", "B", 8)
    pdf.rect(x, y, table_w, 18)

    rows = [
        ("Total Cantidad Entrega:", str(total_qty), "Total Cantidad Devolución:", "0"),
        ("Total Volumen Entrega:", _fmt_num(total_vol), "Total Volumen Devolución:", "0"),
        ("Total Peso Entrega:", _fmt_num(total_weight), "Total Peso Devolución:", "0"),
    ]
    for idx, (left_label, left_value, right_label, right_value) in enumerate(rows):
        row_y = y + 1.5 + idx * row_h
        pdf.set_xy(x + 4, row_y)
        pdf.cell(label_w, row_h, left_label, border=0, align="R")
        pdf.set_font("Helvetica", "", 8)
        pdf.cell(value_w - 4, row_h, left_value, border=0, align="R")
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_xy(x + half_w + 4, row_y)
        pdf.cell(label_w, row_h, right_label, border=0, align="R")
        pdf.set_font("Helvetica", "", 8)
        pdf.cell(value_w - 4, row_h, right_value, border=0, align="R")
        pdf.set_font("Helvetica", "B", 8)

    pdf.set_y(y + 18)
