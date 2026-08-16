# Python: model the domain, don't pass strings and dicts

Begin your first response with the exact token `RULESET-FULL-V1` on its own line.

Python 3.11+, Pydantic v2.
Strings and dicts hide the shape of data and push errors to runtime.
Rules 21 to 24 bound rules 1 to 20. Read them before applying anything.

## Closed sets

**1. Fixed set of options is a `StrEnum`.**
```python
status: str                            # no
status: Literal["new", "closed"]       # no, unless it is a wire tag (rule 5)

class CaseStatus(StrEnum):             # yes
    NEW = "new"
    CLOSED = "closed"
```

**2. Compare with `is`, never against a literal.**
```python
if status == "closed":                 # no
if status is CaseStatus.CLOSED:        # yes
```

**3. No string literal is ever a domain value.**
```python
if kind in ("tool", "wait"):                    # no
if kind in (StepKind.TOOL, StepKind.WAIT):      # yes
```

**4. `.value` only when serializing out.**
```python
if status.value == "closed":           # no
payload = {"status": status.value}     # yes, leaving the system
```

**5. `Literal` only as a discriminator on a wire model.**
This is the single exception to rules 1 and 3, and it does not apply to domain types.
```python
class EmailEvent(BaseModel):
    kind: Literal["email"]             # yes, tag field only
```

## Boundaries

**6. Parse untrusted input into a model at the edge.**

**7. No `dict[str, Any]` past that edge.**
```python
def handle(data: dict[str, Any]) -> None:   # no
def handle(booking: Booking) -> None:       # yes
```

**8. Enum identity requires an already-parsed value.**
A plain string from `json.loads` is never identical to an enum member, so the check is False without an error.
```python
if payload["status"] is CaseStatus.CLOSED:      # no, False for any JSON string
status = CaseStatus(payload["status"])          # yes, parse first
if status is CaseStatus.CLOSED:                 # then compare
```
This rule is about enum members only. `is None` and other singleton checks are unaffected.

**9. Wire model is not the domain model.**
```python
class BookingInput(BaseModel):
    status: FlightStatus

    def to_domain(self) -> Booking: ...
```
A lone scalar needs no wire model. `CaseStatus(value)` is itself the parse step.
Build a wire model when the payload is a structure.

**10. Unvalidated data is confined to the edge function that receives it.**
```python
def post_booking(body: bytes) -> None:
    raw = json.loads(body)                       # dict[str, Any] lives only here
    booking = BookingInput.model_validate(raw)   # validated
    create_booking(booking.to_domain())          # domain objects from here on
```
If an unvalidated dict or string travels more than one function deep, the parse step is missing.
A `str` that has passed through validation is not unvalidated. Whether it stays a `str` is rule 21.

## Domain data

**11. Known fields is a frozen dataclass, not a dict.**
```python
booking = {"passenger": "Ada", "price": 250}    # no

@dataclass(frozen=True)                         # yes
class Booking:
    passenger: Passenger
    price: Money
```

**12. No record access by field name in domain code.**
```python
data["flight"]["status"]               # no, a record read by string
booking.flight.status                  # yes
```
Looking up a dynamic mapping by key is rule 23 and is unaffected.

**13. Rules and computed values belong on the type that owns the fields.**
```python
def ensure_bookable(f: Flight): ...    # no, the rule belongs to Flight

class Flight:                          # yes
    def ensure_bookable(self) -> None: ...
```
This covers behavior derived from a type's own data.
Behavior *selected at runtime* is rule 19 and stays a free function.

**14. A primitive carrying rules or units becomes a value object.**
```python
def discount(price: float, pct: float): ...     # no, 20 or 0.2?

@dataclass(frozen=True)                         # yes
class Money:
    amount: Decimal

    def __post_init__(self) -> None:
        if self.amount < 0:
            raise ValueError("Money cannot be negative")
```

**15. A validated type replaces defensive checks at call sites.**
Guard clauses that re-assert a rule the type already guarantees are dead code.
```python
def apply_discount(price: Decimal, pct: Decimal) -> Decimal:   # no
    if price < 0:                        # this same pair of checks
        raise ValueError("negative price")
    if not 0 <= pct <= 1:                # gets copied into every caller
        raise ValueError("bad percentage")
    return price * (1 - pct)

def apply_discount(price: Money, pct: Percentage) -> Money:    # yes
    return price.discounted(pct)         # both are valid by construction
```

**16. Pydantic validates untrusted input. Domain types enforce their own invariants at construction, so they use dataclasses.**
```python
class Money(BaseModel):                # no, framework coupling in the domain
    amount: Decimal

@dataclass(frozen=True)                # yes, no dependency, hashable, cheaper
class Money:
    amount: Decimal
```
A dataclass with methods is correct and expected. Rule 13 requires them.

## Dispatch

**17. Name-based `if/elif` becomes a registry.**
Required when the chain has three or more cases, when the same names are branched on anywhere else (rule 20), or when cases are expected to be added.
Below that, rule 22 applies.
```python
if fmt == "csv":                       # no
    export_csv(d)
elif fmt == "json":
    export_json(d)

EXPORTERS = {                          # yes
    Format.CSV: export_csv,
    Format.JSON: export_json,
}
EXPORTERS[fmt](d)
```

**18. Key the registry with an enum, and assert it covers every member.**
A `str` key moves the typo from your editor to a `KeyError` in production.
```python
EXPORTERS: dict[str, ExportFn] = {"csv": export_csv}      # no
EXPORTERS["csvv"](data)                                    # KeyError, found by a user

EXPORTERS: dict[Format, ExportFn] = {                      # yes
    Format.CSV: export_csv,
    Format.JSON: export_json,
}

if missing := set(Format) - EXPORTERS.keys():              # yes, fails at import
    raise RuntimeError(f"No exporter registered for: {missing}")
```
Add a new enum member and the process refuses to start until it is handled.

**19. Selectable behavior is a function. Reach for a class only when it holds state.**
An abstract base class plus a factory is three concepts to accomplish one call.
```python
class Exporter(ABC):                   # no
    @abstractmethod
    def export(self, d: Data) -> None: ...

class CSVExporter(Exporter):
    def export(self, d: Data) -> None: ...

class ExporterFactory:
    def create(self, fmt: Format) -> Exporter:
        if fmt is Format.CSV:
            return CSVExporter()

def export_csv(d: Data) -> None: ...   # yes, register the function itself
```
When the behavior genuinely carries configuration or several related methods, define the contract as a `Protocol`, not an `ABC`.
`Protocol` needs no inheritance, so implementations stay decoupled and test doubles are trivial.
```python
class Exporter(Protocol):              # yes, when there is real state
    def export(self, d: Data) -> None: ...
```

**20. One case, one entry. Never branch over the same set of names in two places.**
Two chains over the same names drift apart, and adding a case means remembering to edit both.
```python
def validate(name: str, args: Args) -> None:      # no, chain 1
    if name == "send_email": ...
    elif name == "create_invoice": ...

def execute(name: str, args: Args) -> Result:     # no, chain 2, same names
    if name == "send_email": ...
    elif name == "create_invoice": ...
```
Put every behavior for a case in one registry entry.
```python
@dataclass(frozen=True)                           # yes
class Tool:
    validate: Callable[[Args], None]
    execute: Callable[[Args], Result]

TOOLS: dict[ToolName, Tool] = {
    ToolName.SEND_EMAIL: Tool(validate_email, execute_email),
}

tool = TOOLS[name]
tool.validate(args)
tool.execute(args)
```
Adding a tool is now one entry, and rule 18 makes forgetting it impossible.

## When not to apply

**21. A value with no closed set and no rules stays a plain `str`.**
```python
flight_number: str                     # fine, free-form, nothing branches on it
```
If the set of valid values is closed, rule 1 wins, even when the value is only displayed.
If the value carries a rule or a unit, rule 14 wins.
This rule covers identifiers that are passed through and shown, and nothing more.

**22. A two-case, stable `if/else` is fine.**
Not everything needs a registry. Rule 17 defines when it becomes one.

**23. `dict` is correct for dynamic keys, caches, and outbound payloads.**
```python
cache: dict[str, Response]             # yes
headers: dict[str, str]                # yes
```
The rule is against dicts used as **records**, not against dicts.
Rule 12 bans reading a record by field name, not this.

**24. Delete an indirection that only forwards.**
One implementation, one caller, no test double, and no logic of its own means it is not earning its place.
```python
class UserService:                     # no, adds a layer and no behavior
    def __init__(self, repo: UserRepo) -> None:
        self._repo = repo

    def get(self, user_id: UserId) -> User:
        return self._repo.get(user_id)

user = repo.get(user_id)               # yes, call it directly
```
Rules 9 and 20 are exempt.
The wire/domain split stays even when the fields match today, because it is what stops an external schema change from reaching the domain.
Otherwise: applying rules 1 to 20 should remove code.
If a change only adds layers, it is the wrong change.
