from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    Boolean,
    Date,
    Float,
    ForeignKey,
    Integer,
    String,
    Time,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base


class Truck(Base):
    __tablename__ = "trucks"

    code: Mapped[str] = mapped_column(String(10), primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    pallet_capacity: Mapped[int] = mapped_column(Integer)
    max_weight_kg: Mapped[float] = mapped_column(Float)
    sides: Mapped[str] = mapped_column(String(20))
    fleet_count: Mapped[int] = mapped_column(Integer)

    day_cases: Mapped[list[DayCase]] = relationship(back_populates="truck")


class Sku(Base):
    __tablename__ = "skus"

    sku: Mapped[str] = mapped_column(String(50), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    uma: Mapped[str] = mapped_column(String(10))
    unit_volume_m3: Mapped[float] = mapped_column(Float)
    unit_weight_kg: Mapped[float] = mapped_column(Float)
    is_returnable: Mapped[bool] = mapped_column(Boolean)
    warehouse_location: Mapped[str | None] = mapped_column(String(50), nullable=True)
    manufacturer: Mapped[str | None] = mapped_column(String(100), nullable=True)


class Client(Base):
    __tablename__ = "clients"

    client_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    address: Mapped[str] = mapped_column(String(300))
    cp: Mapped[str] = mapped_column(String(10))
    city: Mapped[str] = mapped_column(String(100))
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)

    time_windows: Mapped[list[ClientTimeWindow]] = relationship(
        back_populates="client", cascade="all, delete-orphan"
    )


class ClientTimeWindow(Base):
    __tablename__ = "client_time_windows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.client_id"))
    weekday: Mapped[int] = mapped_column(Integer)
    shift: Mapped[int] = mapped_column(Integer)
    start: Mapped[dt.time] = mapped_column(Time)
    end: Mapped[dt.time] = mapped_column(Time)
    closed: Mapped[bool] = mapped_column(Boolean)

    client: Mapped[Client] = relationship(back_populates="time_windows")


class DayCase(Base):
    __tablename__ = "day_cases"
    __table_args__ = (UniqueConstraint("date", "ruta", name="uq_day_ruta"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[dt.date] = mapped_column(Date)
    ruta: Mapped[str] = mapped_column(String(20))
    repartidor: Mapped[str] = mapped_column(String(100))
    truck_code: Mapped[str] = mapped_column(ForeignKey("trucks.code"))
    raw_transports: Mapped[str] = mapped_column(String(500), default="")

    truck: Mapped[Truck] = relationship(back_populates="day_cases")
    orders: Mapped[list[ClientOrder]] = relationship(
        back_populates="day_case", cascade="all, delete-orphan"
    )


class ClientOrder(Base):
    __tablename__ = "client_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    day_case_id: Mapped[int] = mapped_column(ForeignKey("day_cases.id"))
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.client_id"))
    expected_returnable_units: Mapped[float] = mapped_column(Float)
    visit_seq: Mapped[int] = mapped_column(Integer)

    day_case: Mapped[DayCase] = relationship(back_populates="orders")
    client: Mapped[Client] = relationship()
    lines: Mapped[list[OrderLine]] = relationship(
        back_populates="client_order", cascade="all, delete-orphan"
    )


class OrderLine(Base):
    __tablename__ = "order_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_order_id: Mapped[int] = mapped_column(ForeignKey("client_orders.id"))
    sku_code: Mapped[str] = mapped_column(ForeignKey("skus.sku"))
    qty: Mapped[float] = mapped_column(Float)
    uma: Mapped[str] = mapped_column(String(10))
    unit_volume_m3: Mapped[float] = mapped_column(Float)
    unit_weight_kg: Mapped[float] = mapped_column(Float)
    is_returnable: Mapped[bool] = mapped_column(Boolean)

    client_order: Mapped[ClientOrder] = relationship(back_populates="lines")
    sku: Mapped[Sku] = relationship()
