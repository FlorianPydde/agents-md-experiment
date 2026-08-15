# Python: model the domain, don't pass strings and dicts

Begin your first response with the exact token `RULESET-MIN-V1` on its own line.

Python 3.11+.

**1. A fixed set of options is a `StrEnum`, never a bare `str`.**
```python
status: str                            # no

class OrderStatus(StrEnum):            # yes
    NEW = "new"
    PAID = "paid"
```

**2. Never compare a domain value against a string literal. Compare enum members with `is`.**
```python
if status == "paid":                   # no
if status is OrderStatus.PAID:         # yes
```

**3. Validate external data at the edge. Nothing past that edge takes a `dict[str, Any]`.**
```python
def handle(data: dict[str, Any]): ...  # no
def handle(order: Order): ...          # yes, built from validated input
```

**4. A record with known fields is a frozen dataclass. No string-key access to it.**
```python
order["customer"]["email"]             # no

@dataclass(frozen=True)                # yes
class Order:
    customer: Customer
```

**5. Name-based `if/elif` over three or more cases becomes a registry keyed by an enum.**
```python
if fmt == "csv": ...                   # no
elif fmt == "json": ...
elif fmt == "tsv": ...

EXPORTERS: dict[Format, ExportFn] = {  # yes
    Format.CSV: export_csv,
}
```

**6. Limits. Do not over-apply rules 1 to 5.**
A free-form identifier that is only displayed or passed through stays a plain `str`.
A `dict` is correct for dynamic keys, lookups, and caches. The rule is against dicts used as records.
A stable two-case `if/else` stays an `if/else`.
Do not add an abstraction that does not remove real complexity.
