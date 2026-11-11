"""Public input contract. Timestamp comparisons are UTC; delivery promises are Brazilian dates."""

import hashlib
import json
from datetime import UTC, date, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

LOCAL_TZ = ZoneInfo("America/Sao_Paulo")
SAFE_ID = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"


def instant(value: str | datetime) -> datetime:
    result = datetime.fromisoformat(value) if isinstance(value, str) else value
    if result.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return result.astimezone(UTC)


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    ).hexdigest()


class Row(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Customer(Row):
    customer_id: str = Field(pattern=SAFE_ID)
    customer_state: str = Field(pattern=r"^[A-Z]{2}$")


class Seller(Row):
    seller_id: str = Field(pattern=SAFE_ID)
    seller_state: str = Field(pattern=r"^[A-Z]{2}$")


class Product(Row):
    product_id: str = Field(pattern=SAFE_ID)
    category: str = Field(min_length=1, max_length=100)
    weight_g: float = Field(ge=0, le=1_000_000)


class Order(Row):
    order_id: str = Field(pattern=SAFE_ID)
    customer_id: str = Field(pattern=SAFE_ID)
    approved_at: datetime
    promised_date: date

    @field_validator("approved_at")
    @classmethod
    def aware(cls, value):
        return instant(value)

    @model_validator(mode="after")
    def promise_after_approval(self):
        if self.promised_date < self.approved_at.astimezone(LOCAL_TZ).date():
            raise ValueError("promise precedes approval")
        return self


class Item(Row):
    order_id: str = Field(pattern=SAFE_ID)
    item_id: int = Field(ge=1)
    product_id: str = Field(pattern=SAFE_ID)
    seller_id: str = Field(pattern=SAFE_ID)
    price: float = Field(ge=0, le=10_000_000)
    freight: float = Field(ge=0, le=10_000_000)


class Payment(Row):
    order_id: str = Field(pattern=SAFE_ID)
    sequence: int = Field(ge=1)
    payment_type: str = Field(min_length=1, max_length=40)
    installments: int = Field(ge=0, le=100)
    value: float = Field(ge=0, le=10_000_000)


class Outcome(Row):
    order_id: str = Field(pattern=SAFE_ID)
    delivered_at: datetime
    available_at: datetime

    @field_validator("delivered_at", "available_at")
    @classmethod
    def aware(cls, value):
        return instant(value)

    @model_validator(mode="after")
    def not_available_before_delivery(self):
        if self.available_at < self.delivered_at:
            raise ValueError("outcome cannot become available before delivery")
        return self


class Batch(Row):
    schema_version: Literal["1.0"] = "1.0"
    batch_id: str = Field(pattern=SAFE_ID)
    available_at: datetime
    kind: Literal["orders", "outcomes"]
    customers: list[Customer] = Field(default_factory=list)
    sellers: list[Seller] = Field(default_factory=list)
    products: list[Product] = Field(default_factory=list)
    orders: list[Order] = Field(default_factory=list)
    items: list[Item] = Field(default_factory=list)
    payments: list[Payment] = Field(default_factory=list)
    outcomes: list[Outcome] = Field(default_factory=list)

    @field_validator("available_at")
    @classmethod
    def aware(cls, value):
        return instant(value)

    @model_validator(mode="after")
    def valid_kind(self):
        tables = (self.customers, self.sellers, self.products, self.orders, self.items, self.payments)
        if self.kind == "orders" and (self.outcomes or not self.orders):
            raise ValueError("order batches require orders and forbid outcomes")
        if self.kind == "outcomes" and (any(tables) or not self.outcomes):
            raise ValueError("outcome batches contain outcomes only")
        if any(x.approved_at > self.available_at for x in self.orders):
            raise ValueError("batch contains an order not yet approved")
        if any(x.available_at > self.available_at for x in self.outcomes):
            raise ValueError("batch releases a future outcome")
        return self


class PredictionRequest(Row):
    order_id: str = Field(pattern=SAFE_ID)
    request_id: str | None = Field(default=None, pattern=SAFE_ID)


class PredictionResponse(Row):
    order_id: str
    request_id: str
    risk_probability: float = Field(ge=0, le=1)
    model_version: str
