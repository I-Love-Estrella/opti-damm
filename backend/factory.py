"""Seed the database from Excel source files.

Usage:
    python -m backend.factory          # seed (skip if data exists)
    python -m backend.factory --force  # drop and re-seed
"""

from __future__ import annotations

import sys

from sqlalchemy import inspect

from backend.database import Base, SessionLocal, engine
from backend.models import (
    Client,
    ClientOrder,
    ClientTimeWindow,
    DayCase,
    OrderLine,
    Sku,
    Truck,
)
from simulator.config import TRUCK_SPECS
from simulator.data.catalog import Catalog
from simulator.data.clients import Clients
from simulator.data.loader import load_all
from simulator.data.orders import DayCaseBuilder


def seed(force: bool = False) -> None:
    if force:
        Base.metadata.drop_all(engine)

    Base.metadata.create_all(engine)

    db = SessionLocal()
    try:
        if db.query(Truck).count() > 0 and not force:
            print("Database already seeded. Use --force to re-seed.")
            return

        print("Loading data from Excel files...")
        raw = load_all()
        catalog = Catalog.build(raw)
        clients = Clients.build(raw)
        builder = DayCaseBuilder(raw, catalog, clients)

        _seed_trucks(db)
        _seed_skus(db, catalog)
        _seed_clients(db, clients)
        _seed_day_cases(db, builder, catalog, clients)

        db.commit()
        print("Database seeded successfully.")
        _print_summary(db)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _seed_trucks(db) -> None:
    for spec in TRUCK_SPECS.values():
        db.add(Truck(
            code=spec.code,
            name=spec.name,
            pallet_capacity=spec.pallet_capacity,
            max_weight_kg=spec.max_weight_kg,
            sides=",".join(spec.sides),
            fleet_count=spec.fleet_count,
        ))
    db.flush()
    print(f"  Trucks: {len(TRUCK_SPECS)}")


def _seed_skus(db, catalog: Catalog) -> None:
    records = catalog.all()
    for rec in records.values():
        db.add(Sku(
            sku=rec.sku,
            name=rec.name,
            uma=rec.uma,
            unit_volume_m3=rec.unit_volume_m3,
            unit_weight_kg=rec.unit_weight_kg,
            is_returnable=rec.is_returnable,
            warehouse_location=rec.warehouse_location,
            manufacturer=rec.manufacturer,
        ))
    db.flush()
    print(f"  SKUs: {len(records)}")


def _seed_clients(db, clients: Clients) -> None:
    all_clients = clients.all()
    for rec in all_clients.values():
        client = Client(
            client_id=rec.client_id,
            name=rec.name,
            address=rec.address,
            cp=rec.cp,
            city=rec.city,
            lat=rec.lat,
            lon=rec.lon,
        )
        for tw in rec.time_windows:
            client.time_windows.append(ClientTimeWindow(
                weekday=tw.weekday,
                shift=tw.shift,
                start=tw.start,
                end=tw.end,
                closed=tw.closed,
            ))
        db.add(client)
    db.flush()
    print(f"  Clients: {len(all_clients)}")


def _seed_day_cases(db, builder: DayCaseBuilder, catalog: Catalog, clients: Clients) -> None:
    listing = builder.list_day_cases(min_clients=1)
    n_cases = 0
    n_orders = 0
    n_lines = 0

    for _, row in listing.iterrows():
        fecha = row["fecha"]
        ruta = str(row["Ruta"])
        case = builder.build(fecha, ruta)

        # Ensure all client_ids from orders exist in the clients table
        for order in case.orders:
            existing = db.get(Client, order.client_id)
            if existing is None:
                rec = clients.get(order.client_id)
                db.add(Client(
                    client_id=rec.client_id,
                    name=rec.name,
                    address=rec.address,
                    cp=rec.cp,
                    city=rec.city,
                    lat=rec.lat,
                    lon=rec.lon,
                ))
                db.flush()

        day_case = DayCase(
            date=case.date,
            ruta=case.ruta,
            repartidor=case.repartidor,
            truck_code=case.truck.code,
            raw_transports=",".join(case.raw_transports),
        )

        for order in case.orders:
            client_order = ClientOrder(
                client_id=order.client_id,
                expected_returnable_units=order.expected_returnable_units,
                visit_seq=order.visit_seq_actual,
            )
            for line in order.lines:
                # Ensure SKU exists
                existing_sku = db.get(Sku, line.sku)
                if existing_sku is None:
                    rec = catalog.get(line.sku)
                    db.add(Sku(
                        sku=rec.sku,
                        name=rec.name,
                        uma=rec.uma,
                        unit_volume_m3=rec.unit_volume_m3,
                        unit_weight_kg=rec.unit_weight_kg,
                        is_returnable=rec.is_returnable,
                        warehouse_location=rec.warehouse_location,
                        manufacturer=rec.manufacturer,
                    ))
                    db.flush()

                client_order.lines.append(OrderLine(
                    sku_code=line.sku,
                    qty=line.qty,
                    uma=line.uma,
                    unit_volume_m3=line.unit_volume_m3,
                    unit_weight_kg=line.unit_weight_kg,
                    is_returnable=line.is_returnable,
                ))
                n_lines += 1
            day_case.orders.append(client_order)
            n_orders += 1
        db.add(day_case)
        n_cases += 1

    db.flush()
    print(f"  Day cases: {n_cases}")
    print(f"  Client orders: {n_orders}")
    print(f"  Order lines: {n_lines}")


def _print_summary(db) -> None:
    print("\n--- Database Summary ---")
    print(f"  Trucks:        {db.query(Truck).count()}")
    print(f"  SKUs:          {db.query(Sku).count()}")
    print(f"  Clients:       {db.query(Client).count()}")
    print(f"  Time windows:  {db.query(ClientTimeWindow).count()}")
    print(f"  Day cases:     {db.query(DayCase).count()}")
    print(f"  Orders:        {db.query(ClientOrder).count()}")
    print(f"  Order lines:   {db.query(OrderLine).count()}")


if __name__ == "__main__":
    force = "--force" in sys.argv
    seed(force=force)
