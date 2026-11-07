"""後日作成の人工市場イベントによる構造説明用シミュレーション。PPO推論・実売買・当時の成績を再現しない。

This auxiliary example is deliberately separate from the historical trading
system. It uses only fixed in-memory values and has no network, credential,
model, environment, or file access.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math


AUXILIARY_TITLE = (
    "後日作成の人工市場イベントによる構造説明用シミュレーション。"
    "PPO推論・実売買・当時の成績を再現しない"
)


def _is_finite_number(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(float(value))
    except (OverflowError, ValueError):
        return False


@dataclass(frozen=True)
class MarketEvent:
    """One artificial market snapshot containing scalar values only."""

    price: float
    volume_per_sec: float
    bid_depth: float
    ask_depth: float
    spread_ticks: float

    def __post_init__(self) -> None:
        for name in ("price", "volume_per_sec", "bid_depth", "ask_depth", "spread_ticks"):
            value = getattr(self, name)
            if not _is_finite_number(value):
                raise ValueError(f"{name} must be a finite number")
        if self.price <= 0:
            raise ValueError("price must be positive")
        if self.volume_per_sec < 0 or self.bid_depth < 0 or self.ask_depth < 0:
            raise ValueError("volume and depth values must be nonnegative")
        if self.spread_ticks < 0:
            raise ValueError("spread_ticks must be nonnegative")


@dataclass(frozen=True)
class OrderIntent:
    """A local simulation intent. It is never sent to a broker service."""

    side: str
    quantity: int


@dataclass(frozen=True)
class Fill:
    """An in-memory simulated fill returned by MockBroker."""

    fill_number: int
    side: str
    quantity: int
    price: float

    def __post_init__(self) -> None:
        if isinstance(self.fill_number, bool) or not isinstance(self.fill_number, int) or self.fill_number <= 0:
            raise ValueError("fill_number must be a positive integer")
        if self.side not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL")
        if isinstance(self.quantity, bool) or not isinstance(self.quantity, int) or self.quantity <= 0:
            raise ValueError("quantity must be a positive integer")
        if not _is_finite_number(self.price) or self.price <= 0:
            raise ValueError("price must be a positive finite number")

    @property
    def signed_quantity(self) -> int:
        return self.quantity if self.side == "BUY" else -self.quantity


@dataclass
class PositionLedger:
    """Small average-cost ledger for synthetic fills only."""

    quantity: int = 0
    average_entry_price: float = 0.0
    realized_pnl: float = 0.0

    def apply(self, fill: Fill) -> float:
        """Apply one local fill and return the realized P/L change."""
        if type(fill) is not Fill:
            raise ValueError("fill must be an in-memory Fill value")
        old_quantity = self.quantity
        signed_quantity = fill.signed_quantity
        new_quantity = old_quantity + signed_quantity
        realized_change = 0.0
        new_average_entry_price = self.average_entry_price

        if old_quantity == 0 or old_quantity * signed_quantity > 0:
            old_units = abs(old_quantity)
            added_units = abs(signed_quantity)
            total_units = old_units + added_units
            new_average_entry_price = (
                old_units * self.average_entry_price + added_units * fill.price
            ) / total_units
        else:
            closed_units = min(abs(old_quantity), abs(signed_quantity))
            direction = 1 if old_quantity > 0 else -1
            realized_change = closed_units * (fill.price - self.average_entry_price) * direction
            if new_quantity == 0:
                new_average_entry_price = 0.0
            elif old_quantity * new_quantity < 0:
                new_average_entry_price = fill.price

        if not math.isfinite(float(new_average_entry_price)) or not math.isfinite(float(realized_change)):
            raise ValueError("ledger calculation must remain finite")
        new_realized_pnl = self.realized_pnl + realized_change
        if not math.isfinite(float(new_realized_pnl)):
            raise ValueError("ledger realized P/L must remain finite")

        self.quantity = new_quantity
        self.average_entry_price = new_average_entry_price
        self.realized_pnl = new_realized_pnl
        return realized_change


@dataclass(frozen=True)
class RiskGate:
    """Simulation-only position and realized-loss guard."""

    max_units: int = 2
    loss_limit: float = 100.0

    def __post_init__(self) -> None:
        if isinstance(self.max_units, bool) or not isinstance(self.max_units, int) or self.max_units <= 0:
            raise ValueError("max_units must be a positive integer")
        if not _is_finite_number(self.loss_limit) or self.loss_limit <= 0:
            raise ValueError("loss_limit must be a positive finite number")

    def rejection_reason(
        self,
        intent: OrderIntent,
        current_quantity: int,
        realized_pnl: float,
    ) -> str | None:
        """Return a rejection code without mutating the intent or ledger."""
        if type(intent) is not OrderIntent:
            return "INVALID_ORDER"
        if intent.side not in {"BUY", "SELL"}:
            return "INVALID_SIDE"
        if isinstance(intent.quantity, bool) or not isinstance(intent.quantity, int) or intent.quantity <= 0:
            return "INVALID_QUANTITY"
        if isinstance(current_quantity, bool) or not isinstance(current_quantity, int):
            return "INVALID_POSITION"
        if not _is_finite_number(realized_pnl):
            return "INVALID_PNL"

        signed_quantity = intent.quantity if intent.side == "BUY" else -intent.quantity
        proposed_quantity = current_quantity + signed_quantity
        reduces_exposure = abs(proposed_quantity) < abs(current_quantity)

        if abs(proposed_quantity) > self.max_units and not reduces_exposure:
            return "MAX_UNITS"
        if realized_pnl <= -self.loss_limit and not reduces_exposure:
            return "LOSS_LIMIT"
        return None


class PolicyStub:
    """Deterministic threshold policy created for this offline example."""

    def __init__(self, buy_units: int = 1, momentum_threshold: float = 0.005, heat_threshold: float = 0.4):
        if isinstance(buy_units, bool) or not isinstance(buy_units, int) or buy_units <= 0:
            raise ValueError("buy_units must be a positive integer")
        for name, value in (("momentum_threshold", momentum_threshold), ("heat_threshold", heat_threshold)):
            if not _is_finite_number(value):
                raise ValueError(f"{name} must be a finite number")
        self.buy_units = buy_units
        self.momentum_threshold = float(momentum_threshold)
        self.heat_threshold = float(heat_threshold)

    def decide(self, features: dict[str, float], position_quantity: int) -> OrderIntent | None:
        momentum = features["momentum"]
        market_heat = features["market_heat"]
        if position_quantity == 0 and momentum >= self.momentum_threshold and market_heat >= self.heat_threshold:
            return OrderIntent("BUY", self.buy_units)
        if position_quantity > 0 and momentum < 0:
            return OrderIntent("SELL", position_quantity)
        if position_quantity < 0 and momentum > 0:
            return OrderIntent("BUY", abs(position_quantity))
        return None


class MockBroker:
    """Returns deterministic fills in memory; it has no external connection."""

    def execute(self, intent: OrderIntent, event: MarketEvent, fill_number: int) -> Fill:
        return Fill(fill_number, intent.side, intent.quantity, float(event.price))


@dataclass(frozen=True)
class SimulationReport:
    title: str
    features: tuple[dict[str, float], ...]
    event_log: tuple[dict[str, object], ...]
    fills: tuple[dict[str, object], ...]
    final_quantity: int
    average_entry_price: float
    realized_pnl: float

    def as_dict(self) -> dict[str, object]:
        return {
            "title": self.title,
            "features": list(self.features),
            "event_log": list(self.event_log),
            "fills": list(self.fills),
            "position": {
                "quantity": self.final_quantity,
                "average_entry_price": self.average_entry_price,
                "realized_pnl": self.realized_pnl,
            },
        }


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _extract_scalar_features(event: MarketEvent, previous_price: float | None) -> dict[str, float]:
    volume_score = _clamp01(float(event.volume_per_sec) / 100.0)
    depth_average = float(event.bid_depth) / 2.0 + float(event.ask_depth) / 2.0
    depth_score = _clamp01(depth_average / 1000.0)
    spread_score = _clamp01(1.0 - float(event.spread_ticks) / 5.0)
    momentum = 0.0 if previous_price is None else (float(event.price) - previous_price) / previous_price
    values = {
        "price": float(event.price),
        "volume_score": volume_score,
        "depth_score": depth_score,
        "spread_score": spread_score,
        "market_heat": (volume_score + depth_score + spread_score) / 3.0,
        "momentum": momentum,
    }
    if not all(math.isfinite(value) for value in values.values()):
        raise ValueError("derived features must be finite")
    return values


class OfflinePipeline:
    """Artificial market → features → policy → risk → mock fill → ledger."""

    def __init__(
        self,
        risk_gate: RiskGate | None = None,
        policy: PolicyStub | None = None,
    ) -> None:
        if risk_gate is not None and type(risk_gate) is not RiskGate:
            raise ValueError("risk_gate must use the local RiskGate implementation")
        if policy is not None and type(policy) is not PolicyStub:
            raise ValueError("policy must use the local PolicyStub implementation")
        self.risk_gate = risk_gate or RiskGate()
        self.policy = policy or PolicyStub()

    def run(self, events: tuple[MarketEvent, ...] | list[MarketEvent]) -> SimulationReport:
        if type(events) not in (tuple, list):
            raise ValueError("events must be a fixed tuple or list of artificial values")
        event_sequence = tuple(events)
        if not all(type(event) is MarketEvent for event in event_sequence):
            raise ValueError("events must contain only artificial MarketEvent values")

        ledger = PositionLedger()
        broker = MockBroker()
        all_features: list[dict[str, float]] = []
        event_log: list[dict[str, object]] = []
        fills: list[dict[str, object]] = []
        previous_price: float | None = None

        for index, event in enumerate(event_sequence, start=1):
            features = _extract_scalar_features(event, previous_price)
            all_features.append(features)
            previous_price = float(event.price)

            before_quantity = ledger.quantity
            before_pnl = ledger.realized_pnl
            intent = self.policy.decide(features, ledger.quantity)
            if intent is None:
                event_log.append({
                    "step": index,
                    "status": "HOLD",
                    "position_before": before_quantity,
                    "position_after": ledger.quantity,
                    "realized_pnl_before": before_pnl,
                    "realized_pnl_after": ledger.realized_pnl,
                })
                continue

            rejection = self.risk_gate.rejection_reason(intent, ledger.quantity, ledger.realized_pnl)
            if rejection is not None:
                event_log.append({
                    "step": index,
                    "status": "REJECTED",
                    "reason": rejection,
                    "side": intent.side,
                    "quantity": intent.quantity,
                    "position_before": before_quantity,
                    "position_after": ledger.quantity,
                    "realized_pnl_before": before_pnl,
                    "realized_pnl_after": ledger.realized_pnl,
                })
                continue

            fill = broker.execute(intent, event, len(fills) + 1)
            realized_change = ledger.apply(fill)
            fill_record: dict[str, object] = {
                "fill_number": fill.fill_number,
                "side": fill.side,
                "quantity": fill.quantity,
                "price": fill.price,
                "realized_pnl_change": realized_change,
            }
            fills.append(fill_record)
            event_log.append({
                "step": index,
                "status": "FILLED",
                "position_before": before_quantity,
                "position_after": ledger.quantity,
                "realized_pnl_before": before_pnl,
                "realized_pnl_after": ledger.realized_pnl,
                "fill_number": fill.fill_number,
            })

        return SimulationReport(
            title=AUXILIARY_TITLE,
            features=tuple(all_features),
            event_log=tuple(event_log),
            fills=tuple(fills),
            final_quantity=ledger.quantity,
            average_entry_price=ledger.average_entry_price,
            realized_pnl=ledger.realized_pnl,
        )


def _demo_events() -> tuple[MarketEvent, ...]:
    return (
        MarketEvent(price=100.0, volume_per_sec=100.0, bid_depth=1000.0, ask_depth=1000.0, spread_ticks=0.0),
        MarketEvent(price=102.0, volume_per_sec=100.0, bid_depth=1000.0, ask_depth=1000.0, spread_ticks=0.0),
        MarketEvent(price=100.0, volume_per_sec=100.0, bid_depth=1000.0, ask_depth=1000.0, spread_ticks=0.0),
    )


def main() -> int:
    report = OfflinePipeline().run(_demo_events())
    print(json.dumps(report.as_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
