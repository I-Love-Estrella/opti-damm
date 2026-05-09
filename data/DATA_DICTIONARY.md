# Data Dictionary

All data originates from Damm's DDI Mollet distribution center (Barcelona area).
Covers delivery operations from **2026-01-30 to 2026-03-31** (2 months).

---

## Source Files

| File | Size | Description |
|------|------|-------------|
| `Hackaton.xlsx` | 6.5 MB | Main operational database (5 sheets) |
| `ZM040.XLSX` | 4.2 MB | Product / material master catalog |
| `Horarios Entrega.XLSX` | 49 KB | Client delivery time windows |
| `Layout Mollet.xlsx` | 203 KB | Warehouse floor-plan (visual grid layout) |

On first run, the simulator converts these to parquet files in `data_cache/` for fast loading.

---

## Hackaton.xlsx

### Sheet: Detalle entrega (82,849 rows)

Every individual product line delivered. One row = one SKU in one delivery.

| Column | Renamed to | Type | Description |
|--------|-----------|------|-------------|
| FECHA | — | date | Delivery date |
| Transporte | — | int | Transport ID (groups multiple deliveries into one truck trip) |
| Ruta | — | string | Route code (e.g. DA0216). 18 unique routes |
| Repartidor | — | int | Driver ID. 18 unique drivers |
| Destinatario mcia. | ClienteName | string | Customer name |
| Entrega | — | int | Delivery ID (one stop at one client) |
| Material | — | string | SKU code (e.g. 0CF0357) |
| Denominacion | — | string | Product description in Spanish |
| Cantidad entrega | — | float | Quantity delivered |
| Un.medida venta | — | string | Sales unit (CAJ=case, UN=unit, BOT=bottle, BAR=barrel, LAT=can, PAL=pallet, BID=keg) |
| Destinatario mcia..1 | ClienteId | string | Customer ID (10-digit) |
| Nombre 1 | — | string | Client business name |
| Nombre 2 | — | string | Client secondary name |
| Calle | — | string | Street address |
| CP | — | string | Postal code (5-digit) |
| Poblacion | — | string | City / town name |
| ZonaTransp | ZonaCliente | string | Transport zone code for this client |
| ZonaTransp.1 | ZonaPoblacion | string | Transport zone name (city-level) |

### Sheet: Cabecera Transporte (8,927 rows)

Transport-level header. One row = one delivery within a transport.

| Column | Type | Description |
|--------|------|-------------|
| Entrega | int | Delivery ID (links to Detalle) |
| No Transporte. | int | Transport number (links to Detalle.Transporte) |
| Creado el | date | Transport creation date |
| Repartidor | int | Driver ID |
| (unnamed col 5) | string | Driver name |
| Destinatario mcia. | string | Customer ID |
| Destinatario mcia..1 | string | Customer name |

### Sheet: Direcciones (1,368 rows)

Client address master. One row = one unique client location.

| Column | Type | Description |
|--------|------|-------------|
| Cliente | string | Client ID (matches ClienteId in Detalle) |
| Nombre 1 | string | Business name |
| Nombre 2 | string | Secondary name |
| Calle | string | Street address |
| CP | string | Postal code. 92 unique codes |
| Poblacion | string | City. 111 unique cities |

### Sheet: ZONAS (1,203 rows)

Zone-to-route mapping and zone metadata.

| Column | Type | Description |
|--------|------|-------------|
| ZONAS | string | Zone code (e.g. DD13100000) |
| NOMBRE ZONAS | string | Zone name (e.g. "MOLLET CAN BORRELL") |
| cliente zona | int | Client ID assigned to this zone |
| ZonaTransp | string | Transport zone code |
| ZonaTransp.1 | string | Zone code (duplicate reference) |
| Zona Entrega | string | Delivery zone name |
| RutReal | string | Actual route code |
| Denominacion | string | Route description |

### Sheet: Materiales zubic (1,489 rows)

Warehouse storage locations for materials at DDI Mollet.

| Column | Type | Description |
|--------|------|-------------|
| Material | string | SKU code |
| Numero de material | string | Material full name |
| Ce. | string | Warehouse center code (D131 = Mollet) |
| Alm. | int | Storage area number |
| UMB | string | Base unit of measure |
| Fabricante | string | Supplier/manufacturer code |
| Numero de un fabricante | string | Supplier name |
| Ubic. | string | Warehouse bin location (e.g. FA05A2) |

---

## ZM040.XLSX (48,457 rows)

Product master catalog with physical dimensions. Multiple rows per material (one per packaging unit).

| Column | Type | Description |
|--------|------|-------------|
| Material | string | SKU code. 7,478 unique products |
| TpMt | string | Material type (ZFIN = finished goods) |
| UMA | string | Unit of measure (CAJ, PAL, BOT, BAR, etc.) |
| Contador | int | Units per UMA |
| Denom. | int | Denomination level |
| Codigo EAN/UPC | string | Barcode |
| Longitud | float | Length (CM) |
| Ancho | float | Width (CM) |
| Altura | float | Height (CM) |
| Volumen | float | Volume (L) |
| Peso bruto | float | Gross weight (KG) |
| Peso neto | float | Net weight (KG) |
| Jquia.productos | string | Product hierarchy code |

---

## Horarios Entrega.XLSX (1,015 rows)

Delivery time windows per client per day of week.

| Column | Type | Description |
|--------|------|-------------|
| Deudor | int | Client ID. 240 unique clients with schedules |
| Organizacion ventas | int | Sales org (235 = Mollet) |
| Canal distribucion | int | Distribution channel |
| Sector | int | Business sector |
| Dia semana | int | Day of week (1=Monday .. 7=Sunday). Days 1-5 and 7 |
| Turno | int | Shift number |
| Nombre 1 | string | Client name |
| Descripcion | string | Facility name (e.g. "DDI MOLLET") |
| Descripcion.1 | string | Channel description |
| Descripcion.2 | string | Facility type |
| Horario inicia a | time | Window open time |
| Horario termina a | time | Window close time |
| Cierre Si/No | string | Closed flag |

---

## Layout Mollet.xlsx

Visual warehouse grid layout for DDI Mollet. Not tabular data -- it's a spatial representation of the warehouse floor with storage positions. The sheets use a grid of cells to map physical locations.

| Sheet | Size | Content |
|-------|------|---------|
| DDI MOLLET | 182 x 62 | Main warehouse floor-plan grid |
| Detalle | 182 x 62 | Detailed version of the floor-plan |
| RESUMEN DDI MOLLET | 6 x 14 | Summary / legend |
| Hoja5 | 4 x 8 | Additional notes |
| Hoja1 | empty | — |

---

## Key Relationships

```
Detalle.Transporte  ──►  Cabecera."No Transporte."
Detalle.Entrega     ──►  Cabecera.Entrega
Detalle.ClienteId   ──►  Direcciones.Cliente
Detalle.Material    ──►  ZM040.Material
Detalle.Material    ──►  Materiales_zubic.Material
Detalle.ZonaCliente ──►  Zonas.ZonaTransp
Horarios.Deudor     ──►  Direcciones.Cliente
```

## Key Metrics

- **82,849** delivery lines across 2 months
- **8,927** individual deliveries
- **889** truck trips (transports)
- **18** routes / drivers
- **1,203** unique client locations
- **92** postal codes across **111** cities
- **1,489** SKUs delivered (from a catalog of 7,478)
