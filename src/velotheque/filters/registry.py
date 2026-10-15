from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from urllib.parse import urlencode

from sqlalchemy import ColumnElement, Connection, Table, func, select


class Entity(StrEnum):
    RIDES = "rides"
    ROUTES = "routes"


@dataclass(frozen=True)
class Field:
    param: str
    label: str
    entities: frozenset[Entity]
    parse: Callable[[str], Any]
    where: Callable[[Table, list[Any]], ColumnElement[bool]]
    multi: bool = False
    facet: Callable[[Table], ColumnElement] | None = None


REGISTRY: dict[str, Field] = {}


def register(f: Field) -> Field:
    if f.param in REGISTRY:
        raise ValueError(f"filter field {f.param!r} registered twice")
    REGISTRY[f.param] = f
    return f


@dataclass
class FilterState:
    values: dict[str, list[Any]] = field(default_factory=dict)
    raw: dict[str, list[str]] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_items(cls, items: Iterable[tuple[str, str]]) -> "FilterState":
        state = cls()
        for param, value in items:
            f = REGISTRY.get(param)
            value = value.strip()
            if f is None or not value:
                continue
            try:
                parsed = f.parse(value)
            except (TypeError, ValueError):
                state.errors[param] = f"invalid value {value!r}"
                continue
            if f.multi:
                if parsed not in state.values.setdefault(param, []):
                    state.values[param].append(parsed)
                    state.raw.setdefault(param, []).append(value)
            else:
                state.values[param] = [parsed]
                state.raw[param] = [value]
        return state

    def get(self, param: str, default=None):
        values = self.values.get(param)
        return values[0] if values else default

    def has(self, param: str, raw_value: str) -> bool:
        return raw_value in self.raw.get(param, [])

    def items(self) -> list[tuple[str, str]]:
        return [(p, v) for p in REGISTRY if p in self.raw for v in self.raw[p]]

    def to_query(self, **extra: str | None) -> str:
        pairs = self.items() + [(k, v) for k, v in extra.items() if v]
        return urlencode(pairs)

    @property
    def active(self) -> bool:
        return bool(self.values)


def build_where(
    state: FilterState, entity: Entity, table: Table, exclude: Iterable[str] = ()
) -> list[ColumnElement[bool]]:
    """AND across fields; each field's where() ORs its own values."""
    skip = set(exclude)
    clauses = []
    for param, values in state.values.items():
        f = REGISTRY[param]
        if param in skip or entity not in f.entities or not values:
            continue
        clauses.append(f.where(table, values))
    return clauses


def facet_counts(
    conn: Connection, state: FilterState, entity: Entity, table: Table, param: str
) -> dict[Any, int]:
    """Counts per value with every active filter except this field's own, so each count
    says what ticking that value would give."""
    f = REGISTRY[param]
    if f.facet is None:
        raise ValueError(f"{param} is not facetable")
    value = f.facet(table).label("value")
    rows = conn.execute(
        select(value, func.count())
        .select_from(table)
        .where(*build_where(state, entity, table, exclude=[param]))
        .group_by(value)
    )
    return dict(rows.all())
