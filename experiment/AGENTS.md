# Python Guidance

## Closed sets

- Use a `StrEnum` for every fixed set of options, and compare members with `is`. A bare `str` accepts any value, so an invalid state is only discovered when it breaks something at runtime. A string literal is also invisible to your tools: rename the enum and every literal keeps compiling while silently meaning nothing. Ruff's `F632` catches `is "literal"`.

```python
status: str                            # no
if status == "paid": ...               # no


class OrderStatus(StrEnum):            # yes
    NEW = "new"
    PAID = "paid"


if status is OrderStatus.PAID: ...     # yes
```

## Moving data and behaviour through the app

1. Use a typed model instead of `dict[str, Any]`, because otherwise no reader and no type checker can tell what is inside, so every use site has to guess.
2. Read a record by attribute, never by string key, because otherwise a mistyped key fails at runtime; a mistyped attribute is caught as you type it.
3. Use dependency injection for collaborator objects, because a branch that picks a collaborator hides a dependency and forces a fake environment in tests. Use a registry only when runtime data selects the behaviour, because otherwise the chain gets duplicated elsewhere and the two copies drift.

```python
def build_report(orders: list[dict[str, Any]], backend: str) -> Report:   # no
    if backend == "postgres":                       # collaborator chosen by a branch
        store = PostgresStore()
    elif backend == "sqlite":
        store = SqliteStore()
    total = sum(o["total"] for o in orders          # string keys, literal compare
                if o["status"] == "paid")


class OrderStore(Protocol):            # the seam: no inheritance, trivial to fake
    def load_all(self) -> list[Order]: ...


def build_report(orders: list[Order], store: OrderStore) -> Report:       # yes
    total = sum(o.total for o in orders             # attributes, enum identity
                if o.status is OrderStatus.PAID)
```

The typed version passes the Protocol `store` in, names what `orders` contains, and reads both fields in a way your editor can check.

```python
EXPORTERS: dict[Format, ExportFn] = {   # yes: behaviour selected by runtime data
    Format.CSV: export_csv,
    Format.JSON: export_json,
    Format.TSV: export_tsv,
}
```

If the keys of a mapping are genuinely dynamic, the values still have a type: write `dict[str, Response]` or `dict[str, str]`, not `Any`.

## Boundaries

- Use Pydantic's `BaseModel` at the boundary. Data arriving from outside is untrusted and has to be validated, which is Pydantic's job. Data crossing a process boundary is a `BaseModel`; everything internal is a frozen dataclass.

```python
class OrderInput(BaseModel):           # boundary: untrusted, validated once
    reference: str
    status: OrderStatus


def process(payload: bytes) -> None:
    order = OrderInput.model_validate(json.loads(payload))   # validated here
    fulfil(order)                                            # typed from here on
```

Validate once at the edge. After that the object is typed, so pass it inward — do **not** create a mirror dataclass and a `to_domain()` that only copies fields across.

Introduce a frozen dataclass only for internal data schemas.

```python
@dataclass(frozen=True)                # internal: invariant lives with the data
class Money:
    amount: Decimal

    def __post_init__(self) -> None:
        if self.amount < 0:
            raise ValueError("Money cannot be negative")
```
