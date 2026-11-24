"""Async client for the (unofficial) LichtBlick app backend.

The LichtBlick app uses Azure AD B2C (ROPC flow) for login and an Apollo
GraphQL gateway for its data. This module is kept free of Home Assistant
imports so it could be moved into a standalone PyPI library later.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
import time
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp

B2C_TOKEN_URL = (
    "https://anmelden.lichtblick.de/lichtblickitb2cprod.onmicrosoft.com"
    "/oauth2/v2.0/token?p=b2c_1a_ropc_signin"
)
B2C_CLIENT_ID = "c6079bc3-9572-4da7-abb7-b69694239976"
B2C_SCOPE = (
    "openid offline_access profile "
    "https://lichtblickitb2cprod.onmicrosoft.com/graph-gateway/app-backend.access "
    "https://lichtblickitb2cprod.onmicrosoft.com/graph-gateway/service-view.access"
)
GRAPH_URL = "https://graph.lichtblick.de/"
GRAPH_HEADERS = {
    "apollographql-client-name": "lichtblick-app-android",
    "apollographql-client-version": "3.9.0",
}
REQUEST_TIMEOUT = 30
TARIFF_TZ = ZoneInfo("Europe/Berlin")
DEFAULT_SLOT = timedelta(minutes=15)

_QUERY_CONTRACTS = """
query SelectableContracts {
  b2cCustomer {
    id
    appSelectableContracts {
      contracts {
        b2cContractId productName category
        address { city postCode street houseNumber }
      }
    }
  }
}
"""

_QUERY_PRICE_CURVES = """
query EnergyPriceCurves($contractId: Int!, $startToday: DateTime!,
                        $endToday: DateTime!, $startTomorrow: DateTime!,
                        $endTomorrow: DateTime!) {
  today: energyPriceCurves(input: { powerCloudContractId: $contractId,
    priceCurveAggregation: DAY, start: $startToday, end: $endToday }) {
    ...Curve
  }
  tomorrow: energyPriceCurves(input: { powerCloudContractId: $contractId,
    priceCurveAggregation: DAY, start: $startTomorrow, end: $endTomorrow }) {
    ...Curve
  }
}
fragment Curve on EnergyPriceCurvesPayload {
  energyPriceCurvesResponse {
    energyPricesInCents { time valueInCents }
    minAvgPriceRange { startTime endTime averagePrice }
    maxAvgPriceRange { startTime endTime averagePrice }
    averagePriceInCents
  }
}
"""


class LichtblickError(Exception):
    """Base exception for the LichtBlick client."""


class LichtblickConnectionError(LichtblickError):
    """Raised when the backend cannot be reached or answers unexpectedly."""


class LichtblickRateLimitError(LichtblickConnectionError):
    """Raised when LichtBlick throttles requests (HTTP 429)."""


class LichtblickAuthError(LichtblickError):
    """Raised when the credentials are rejected."""


@dataclass(frozen=True, slots=True)
class Contract:
    """A power contract of the LichtBlick account."""

    contract_id: int
    product_name: str | None
    city: str | None = None
    post_code: str | None = None
    street: str | None = None
    house_number: str | None = None

    @property
    def title(self) -> str:
        """Human readable title for the contract."""
        parts = [self.product_name or "LichtBlick"]
        address = " ".join(p for p in (self.street, self.house_number) if p)
        place = " ".join(p for p in (self.post_code, self.city) if p)
        location = ", ".join(p for p in (address, place) if p)
        if location:
            parts.append(f"({location})")
        return " ".join(parts)


@dataclass(frozen=True, slots=True)
class PriceSlot:
    """Price of one time slot (usually 15 minutes), in ct/kWh."""

    start: datetime
    end: datetime
    price_ct: float

    @property
    def price_eur(self) -> float:
        """Price in EUR/kWh."""
        return round(self.price_ct / 100, 6)


@dataclass(frozen=True, slots=True)
class PriceWindow:
    """A time window with its average price (cheapest/most expensive block)."""

    start: datetime
    end: datetime
    average_ct: float | None


@dataclass(slots=True)
class DayPrices:
    """All prices of one delivery day."""

    day: date
    slots: list[PriceSlot] = field(default_factory=list)
    average_ct: float | None = None
    cheapest_window: PriceWindow | None = None
    most_expensive_window: PriceWindow | None = None

    @property
    def available(self) -> bool:
        """Return whether prices are published for this day."""
        return bool(self.slots)

    @property
    def min_slot(self) -> PriceSlot | None:
        """Cheapest slot of the day."""
        return min(self.slots, key=lambda s: s.price_ct) if self.slots else None

    @property
    def max_slot(self) -> PriceSlot | None:
        """Most expensive slot of the day."""
        return max(self.slots, key=lambda s: s.price_ct) if self.slots else None


@dataclass(slots=True)
class PriceData:
    """Prices for today and tomorrow."""

    today: DayPrices
    tomorrow: DayPrices

    @property
    def slots(self) -> list[PriceSlot]:
        """All known slots, chronologically."""
        return [*self.today.slots, *self.tomorrow.slots]

    def slot_at(self, moment: datetime) -> PriceSlot | None:
        """Return the slot that contains the given moment."""
        for slot in self.slots:
            if slot.start <= moment < slot.end:
                return slot
        return None

    def next_slot(self, moment: datetime) -> PriceSlot | None:
        """Return the slot following the one that contains the moment."""
        for slot in self.slots:
            if slot.start > moment:
                return slot
        return None


def _parse_dt(value: Any) -> datetime | None:
    """Parse an ISO timestamp; the backend uses year 0001 as 'no value'."""
    if not isinstance(value, str) or not value or value.startswith("0001-"):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TARIFF_TZ)
    return parsed


def _dig(obj: Any, *keys: str) -> Any:
    for key in keys:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def _parse_window(raw: Any) -> PriceWindow | None:
    if not isinstance(raw, dict):
        return None
    start, end = _parse_dt(raw.get("startTime")), _parse_dt(raw.get("endTime"))
    if start is None or end is None:
        return None
    avg = raw.get("averagePrice")
    return PriceWindow(
        start=start,
        end=end,
        average_ct=float(avg) if isinstance(avg, (int, float)) else None,
    )


def parse_day(day: date, payload: Any) -> DayPrices:
    """Convert one energyPriceCurves payload into DayPrices."""
    response = _dig(payload, "energyPriceCurvesResponse") or {}
    points: list[tuple[datetime, float]] = []
    for point in response.get("energyPricesInCents") or []:
        if not isinstance(point, dict):
            continue
        start = _parse_dt(point.get("time"))
        value = point.get("valueInCents")
        if start is None or not isinstance(value, (int, float)):
            continue
        points.append((start, float(value)))
    points.sort(key=lambda p: p[0])

    slots: list[PriceSlot] = []
    for index, (start, value) in enumerate(points):
        if index + 1 < len(points):
            end = points[index + 1][0]
        elif index > 0:
            end = start + (start - points[index - 1][0])
        else:
            end = start + DEFAULT_SLOT
        slots.append(PriceSlot(start=start, end=end, price_ct=value))

    average = response.get("averagePriceInCents")
    if not isinstance(average, (int, float)):
        average = sum(s.price_ct for s in slots) / len(slots) if slots else None

    return DayPrices(
        day=day,
        slots=slots,
        average_ct=float(average) if slots and average is not None else None,
        cheapest_window=_parse_window(response.get("minAvgPriceRange")),
        most_expensive_window=_parse_window(response.get("maxAvgPriceRange")),
    )


class LichtblickClient:
    """Minimal client for the LichtBlick app backend."""

    def __init__(
        self, session: aiohttp.ClientSession, email: str, password: str
    ) -> None:
        """Initialize the client."""
        self._session = session
        self._email = email
        self._password = password
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._expires_at: float = 0
        self._lock = asyncio.Lock()

    async def _post(
        self, url: str, *, data: Any = None, json: Any = None, headers: dict[str, str]
    ) -> tuple[int, Any]:
        try:
            async with self._session.post(
                url,
                data=data,
                json=json,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            ) as resp:
                status = resp.status
                try:
                    body = await resp.json(content_type=None)
                except (aiohttp.ContentTypeError, ValueError):
                    body = None
        except (aiohttp.ClientError, TimeoutError) as err:
            raise LichtblickConnectionError(
                f"Error communicating with LichtBlick: {err}"
            ) from err
        if status == 429:
            raise LichtblickRateLimitError("LichtBlick is rate limiting requests")
        return status, body

    async def _token_request(self, fields: dict[str, str]) -> None:
        status, body = await self._post(
            B2C_TOKEN_URL,
            data={"client_id": B2C_CLIENT_ID, "scope": B2C_SCOPE, **fields},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if isinstance(body, dict) and body.get("access_token"):
            self._access_token = body["access_token"]
            # B2C rotates refresh tokens; keep the old one if none is returned.
            self._refresh_token = body.get("refresh_token") or self._refresh_token
            self._expires_at = time.monotonic() + int(body.get("expires_in", 3600))
            return
        if isinstance(body, dict) and body.get("error"):
            raise LichtblickAuthError(f"Login rejected: {body.get('error')}")
        raise LichtblickConnectionError(f"Unexpected login response (HTTP {status})")

    async def _ensure_token(self, *, force: bool = False) -> str:
        async with self._lock:
            if (
                not force
                and self._access_token
                and self._expires_at > time.monotonic() + 60
            ):
                return self._access_token
            if self._refresh_token:
                try:
                    await self._token_request(
                        {
                            "grant_type": "refresh_token",
                            "refresh_token": self._refresh_token,
                        }
                    )
                except LichtblickAuthError:
                    self._refresh_token = None
                else:
                    assert self._access_token is not None
                    return self._access_token
            await self._token_request(
                {
                    "grant_type": "password",
                    "username": self._email,
                    "password": self._password,
                }
            )
            assert self._access_token is not None
            return self._access_token

    async def _graphql(
        self, query: str, variables: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = variables
        for attempt in range(2):
            token = await self._ensure_token(force=attempt > 0)
            status, body = await self._post(
                GRAPH_URL,
                json=payload,
                headers={
                    **GRAPH_HEADERS,
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {token}",
                },
            )
            if status in (401, 403) or _is_unauthenticated(body):
                if attempt == 0:
                    continue
                raise LichtblickAuthError("Access token rejected by LichtBlick")
            if not isinstance(body, dict):
                raise LichtblickConnectionError(
                    f"Unexpected GraphQL response (HTTP {status})"
                )
            if body.get("errors") and not body.get("data"):
                first = body["errors"][0] if isinstance(body["errors"], list) else {}
                raise LichtblickConnectionError(
                    f"GraphQL error: {str(first.get('message', ''))[:200]}"
                )
            data = body.get("data")
            if not isinstance(data, dict):
                raise LichtblickConnectionError("GraphQL response without data")
            return data
        raise LichtblickAuthError("Access token rejected by LichtBlick")

    async def async_login(self) -> None:
        """Log in and validate the credentials."""
        await self._ensure_token(force=True)

    async def async_get_contracts(self) -> list[Contract]:
        """Return all power contracts of the account."""
        data = await self._graphql(_QUERY_CONTRACTS)
        raw = _dig(data, "b2cCustomer", "appSelectableContracts", "contracts") or []
        contracts: list[Contract] = []
        for item in raw:
            if not isinstance(item, dict) or item.get("category") != "POWER":
                continue
            contract_id = item.get("b2cContractId")
            if not isinstance(contract_id, int):
                continue
            address = item.get("address") or {}
            contracts.append(
                Contract(
                    contract_id=contract_id,
                    product_name=item.get("productName"),
                    city=address.get("city"),
                    post_code=address.get("postCode"),
                    street=address.get("street"),
                    house_number=address.get("houseNumber"),
                )
            )
        return contracts

    async def async_get_prices(
        self, contract_id: int, today: date | None = None
    ) -> PriceData:
        """Return the prices for today and tomorrow (German calendar days)."""
        today = today or datetime.now(TARIFF_TZ).date()
        tomorrow = today + timedelta(days=1)

        def midnight(day: date) -> str:
            return datetime(day.year, day.month, day.day, tzinfo=TARIFF_TZ).isoformat()

        data = await self._graphql(
            _QUERY_PRICE_CURVES,
            {
                "contractId": contract_id,
                "startToday": midnight(today),
                "endToday": midnight(tomorrow),
                "startTomorrow": midnight(tomorrow),
                "endTomorrow": midnight(tomorrow + timedelta(days=1)),
            },
        )
        return PriceData(
            today=parse_day(today, data.get("today")),
            tomorrow=parse_day(tomorrow, data.get("tomorrow")),
        )


def _is_unauthenticated(body: Any) -> bool:
    if not isinstance(body, dict) or not isinstance(body.get("errors"), list):
        return False
    return any(
        isinstance(err, dict)
        and _dig(err, "extensions", "code") in ("UNAUTHENTICATED", "FORBIDDEN")
        for err in body["errors"]
    )
