from __future__ import annotations

from fpdf import FPDF

from simulator.data.catalog import Catalog
from simulator.data.clients import ClientRecord
from simulator.data.orders import ClientOrder, DayCase

COMPANY_NAME = "Distri.de Begudes Movi SL"
COMPANY_ADDR = "C/Molí de Can Bassa, Nau Damm 1"
COMPANY_ADDR2 = "Pol. Ind. Can Magarola"
COMPANY_CITY = "08100,MOLLET DEL VALLÈS"
COMPANY_PHONE = "935939309"
COMPANY_CIF = "B59477968"
COMPANY_EMAIL = "ddimollet@ddidistribucion.com"


def _fmt_date(d) -> str:
    if hasattr(d, "strftime"):
        return d.strftime("%d.%m.%Y")
    parts = str(d).split("-")
    return f"{parts[2]}.{parts[1]}.{parts[0]}"


def _fmt_num(val: float, decimals: int = 2) -> str:
    formatted = f"{val:,.{decimals}f}"
    return formatted.replace(",", "X").replace(".", ",").replace("X", ".")


LEGAL_LINES = [
    "Esta factura se considerará pagada sólo con su correspondiente comprobante de cobro emitido por Distri.de Begudes Movi SL",
    "Este documento se debe sellar o firmar (indicando nombre y DNI) por el cliente de la recepción del producto.",
    "Inscrita en el R.M. de Barcelona Tom 20477 Foli 180 Full 7579, RSIPAC 40 08503/CAT, RGSEAA 40 25351/B.",
    "IBAN: ES65 2100 0102 9102 0085 6284 - SWIFT: CAIXESBBXXX",
]

TABLE_WIDTH = 190
MAX_LINES_PER_PAGE = 28


def generate_albaran(
    case: DayCase,
    order: ClientOrder,
    client: ClientRecord | None,
    catalog: Catalog | None = None,
) -> bytes:
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=False)

    lines = list(order.lines)
    total_pages = max(1, (len(lines) + MAX_LINES_PER_PAGE - 1) // MAX_LINES_PER_PAGE)

    for page_idx in range(total_pages):
        pdf.add_page()
        start = page_idx * MAX_LINES_PER_PAGE
        end = min(start + MAX_LINES_PER_PAGE, len(lines))
        page_lines = lines[start:end]
        is_last = (page_idx == total_pages - 1)

        _header(pdf, case, order, client, page_idx + 1, total_pages)
        _meta_row(pdf, case, order)
        _items_table(pdf, page_lines, catalog)

        if is_last:
            _tax_summary(pdf, order)
            _legal_footer(pdf)

    return pdf.output()


def _header(
    pdf: FPDF, case: DayCase, order: ClientOrder,
    client: ClientRecord | None, page: int, total_pages: int,
) -> None:
    y_start = pdf.get_y()

    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(60, 4, COMPANY_NAME, new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 7)
    pdf.cell(60, 3.5, COMPANY_ADDR, new_x="LMARGIN", new_y="NEXT")
    pdf.cell(60, 3.5, COMPANY_ADDR2, new_x="LMARGIN", new_y="NEXT")
    pdf.cell(60, 3.5, COMPANY_CITY, new_x="LMARGIN", new_y="NEXT")
    pdf.cell(60, 3.5, COMPANY_PHONE, new_x="LMARGIN", new_y="NEXT")
    pdf.cell(60, 3.5, COMPANY_CIF, new_x="LMARGIN", new_y="NEXT")
    pdf.cell(60, 3.5, COMPANY_EMAIL, new_x="LMARGIN", new_y="NEXT")

    y_after_company = pdf.get_y()

    pdf.set_xy(70, y_start)
    pdf.set_font("Helvetica", "B", 7)
    pdf.cell(60, 4, "Razón Social", new_x="LEFT", new_y="NEXT")
    pdf.set_xy(70, pdf.get_y())
    pdf.set_font("Helvetica", "", 7)
    name = (client.name if client else order.client_id)[:30]
    pdf.cell(60, 3.5, name, new_x="LEFT", new_y="NEXT")
    pdf.set_xy(70, pdf.get_y())
    if client:
        pdf.cell(60, 3.5, client.address[:40], new_x="LEFT", new_y="NEXT")
        pdf.set_xy(70, pdf.get_y())
        pdf.cell(60, 3.5, f"{client.cp},{client.city}", new_x="LEFT", new_y="NEXT")
        pdf.set_xy(70, pdf.get_y())

    pdf.set_font("Helvetica", "B", 7)
    pdf.cell(60, 4, "Dirección Entrega", new_x="LEFT", new_y="NEXT")
    pdf.set_xy(70, pdf.get_y())
    pdf.set_font("Helvetica", "", 7)
    pdf.cell(60, 3.5, name, new_x="LEFT", new_y="NEXT")
    pdf.set_xy(70, pdf.get_y())
    if client:
        pdf.cell(60, 3.5, client.address[:40], new_x="LEFT", new_y="NEXT")
        pdf.set_xy(70, pdf.get_y())
        pdf.cell(60, 3.5, f"{client.cp},{client.city}", new_x="LEFT", new_y="NEXT")

    pdf.set_xy(145, y_start)
    pdf.set_font("Helvetica", "", 7)
    pdf.cell(55, 3.5, "Forma de Pago", new_x="LEFT", new_y="NEXT")
    pdf.set_xy(145, pdf.get_y())
    pdf.cell(55, 3.5, "CRÉDITO", new_x="LEFT", new_y="NEXT")
    pdf.set_xy(145, pdf.get_y())
    pdf.cell(55, 3.5, "Recibo Domiciliado", new_x="LEFT", new_y="NEXT")
    pdf.set_xy(145, pdf.get_y())
    pdf.cell(55, 3.5, f"Fecha vto:{_fmt_date(case.date)}", new_x="LEFT", new_y="NEXT")
    pdf.set_xy(145, pdf.get_y())
    pdf.cell(55, 3.5, "Resp:ADMINISTRACIÓN", new_x="LEFT", new_y="NEXT")
    pdf.set_xy(145, pdf.get_y())
    pdf.cell(55, 3.5, "Horario Servicio:", new_x="LEFT", new_y="NEXT")
    pdf.set_xy(145, pdf.get_y())
    pdf.ln(2)

    pdf.set_xy(145, pdf.get_y())
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(55, 8, "Albarán-Factura", new_x="LEFT", new_y="NEXT")

    pdf.set_xy(180, pdf.get_y())
    pdf.set_font("Helvetica", "", 8)
    pdf.cell(20, 5, f"{page}/{total_pages}", align="C")

    pdf.set_y(max(y_after_company, pdf.get_y()) + 4)


ITEM_COL_W = [15, 55, 10, 10, 15, 13, 10, 10, 10, 16, 12, 14]


def _meta_row(pdf: FPDF, case: DayCase, order: ClientOrder) -> None:
    transport = ", ".join(case.raw_transports) or "-"
    col_w = [22, 22, 22, 22, 22, 12, 16, 16, 18, 18]
    labels = ["Número", "Albarán", "Fecha", "Cliente", "N.ºCarga",
              "Viaje", "Cial.", "Vend.", "Ruta", "Rep."]

    pdf.set_font("Helvetica", "B", 6)
    pdf.set_fill_color(220, 220, 220)
    for i, lbl in enumerate(labels):
        pdf.cell(col_w[i], 5, lbl, border=1, fill=True, align="C")
    pdf.ln()

    pdf.set_font("Helvetica", "", 6)
    values = ["", "", _fmt_date(case.date), order.client_id[:14], transport[:14],
              "01", "", "", case.ruta, case.repartidor[:8]]
    for i, val in enumerate(values):
        pdf.cell(col_w[i], 5, val, border=1, align="C")
    pdf.ln(6)


def _items_table(pdf: FPDF, lines, catalog: Catalog | None) -> None:
    headers = ["Producto", "", "UM", "Cdad.", "Precio", "Dto", "P.V.", "I.P.", "I.A.", "Importe", "IVA", "Promoción"]

    pdf.set_font("Helvetica", "B", 6)
    pdf.set_fill_color(220, 220, 220)
    for i, h in enumerate(headers):
        pdf.cell(ITEM_COL_W[i], 5, h, border=1, fill=True, align="C")
    pdf.ln()

    pdf.set_font("Helvetica", "", 6)
    for line in lines:
        desc = ""
        if catalog and line.sku in catalog:
            desc = catalog.get(line.sku).name[:35]

        line_weight = line.qty * line.unit_weight_kg
        row = [
            line.sku,
            desc,
            line.uma,
            f"{line.qty:.0f}",
            _fmt_num(line.unit_weight_kg),
            "",
            "",
            "",
            "",
            _fmt_num(line_weight),
            "",
            "",
        ]
        for i, val in enumerate(row):
            pdf.cell(ITEM_COL_W[i], 5, val, border=1)
        pdf.ln()


def _tax_summary(pdf: FPDF, order: ClientOrder) -> None:
    pdf.ln(2)
    total_weight = order.total_weight_kg

    tax_col_w = [15, 10, 15, 20, 12, 18, 12, 18]
    tax_w = sum(tax_col_w)
    right_w = TABLE_WIDTH - tax_w
    x_left = pdf.l_margin

    labels = ["Imp. Bruto", "S.L.", "Dto. Fact.", "Base", "%IVA", "Imp. IVA", "% RE", "Imp. REC"]

    pdf.set_font("Helvetica", "B", 6)
    for i, lbl in enumerate(labels):
        pdf.cell(tax_col_w[i], 5, lbl, border=1, align="C")
    pdf.ln()

    pdf.set_font("Helvetica", "", 6)
    for i in range(len(tax_col_w)):
        pdf.cell(tax_col_w[i], 5, "", border=1)
    pdf.ln()

    y_after_tax = pdf.get_y()

    pdf.set_xy(x_left + tax_w, y_after_tax - 10)
    pdf.set_font("Helvetica", "", 8)
    pdf.cell(right_w, 10, "Importe", border="LR")
    pdf.set_xy(x_left + tax_w + right_w - 25, y_after_tax - 10)
    pdf.cell(25, 10, _fmt_num(total_weight), border=0, align="R")

    pdf.set_xy(x_left + tax_w, y_after_tax)
    pdf.ln(2)
    pdf.set_xy(x_left + tax_w, pdf.get_y())
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(right_w, 10, "TOTAL", border=1)
    pdf.set_xy(x_left + tax_w + right_w - 25, pdf.get_y())
    pdf.cell(25, 10, _fmt_num(total_weight), border=0, align="R")
    pdf.ln(12)


def _legal_footer(pdf: FPDF) -> None:
    pdf.ln(4)
    pdf.set_font("Helvetica", "", 5.5)
    for line in LEGAL_LINES:
        pdf.cell(0, 3.5, line, new_x="LMARGIN", new_y="NEXT")
