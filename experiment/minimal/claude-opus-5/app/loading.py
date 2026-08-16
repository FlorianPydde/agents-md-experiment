"""The edge: JSON from outside becomes validated domain records or a clear error."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .domain import (
    Account,
    Decision,
    DecisionEntry,
    Origin,
    Requester,
    RequestKind,
    Role,
    ScenarioAction,
    ServiceRequest,
    Tier,
    parse_member,
)
from .errors import InputError
from .money import parse_amount


@dataclass(frozen=True, slots=True)
class Scenario:
    """The ordered things that happen, already validated."""

    entries: tuple[ServiceRequest | DecisionEntry, ...]


def _read_json(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise InputError(f"file not found: {path}") from None
    except OSError as error:
        raise InputError(f"cannot read {path}: {error.strerror}") from None
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise InputError(f"{path} is not valid JSON: {error.msg} at line {error.lineno}") from None


def _mapping(value: Any, *, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise InputError(f"{where} must be an object")
    return value


def _sequence(value: Any, *, where: str) -> Sequence[Any]:
    if not isinstance(value, list):
        raise InputError(f"{where} must be a list")
    return value


def _text(source: Mapping[str, Any], key: str, *, where: str) -> str:
    if key not in source:
        raise InputError(f"{where} is missing the required field {key!r}")
    value = source[key]
    if not isinstance(value, str) or not value.strip():
        raise InputError(f"{where} field {key!r} must be non empty text")
    return value


def load_world(path: Path) -> tuple[Account, ...]:
    """Read the starting state of the accounts."""
    document = _mapping(_read_json(path), where=str(path))
    if "accounts" not in document:
        raise InputError(f"{path} is missing the required field 'accounts'")
    raw_accounts = _sequence(document["accounts"], where=f"{path} 'accounts'")
    accounts: list[Account] = []
    seen: set[str] = set()
    for position, raw in enumerate(raw_accounts):
        where = f"{path} account #{position + 1}"
        entry = _mapping(raw, where=where)
        account_id = _text(entry, "id", where=where)
        if account_id in seen:
            raise InputError(f"{path} lists account {account_id} more than once")
        seen.add(account_id)
        owner = _text(entry, "owner", where=where)
        tier = parse_member(Tier, entry.get("tier"), field=f"{where} 'tier'")
        balance = parse_amount(_text(entry, "balance", where=where), field=f"{where} 'balance'")
        if "frozen" not in entry:
            raise InputError(f"{where} is missing the required field 'frozen'")
        frozen = entry["frozen"]
        if not isinstance(frozen, bool):
            raise InputError(f"{where} field 'frozen' must be true or false")
        accounts.append(
            Account(id=account_id, owner=owner, tier=tier, balance=balance, frozen=frozen)
        )
    if not accounts:
        raise InputError(f"{path} holds no accounts")
    return tuple(accounts)


def _load_request(raw: Any, *, where: str) -> ServiceRequest:
    entry = _mapping(raw, where=where)
    requester = _mapping(entry.get("requester", None), where=f"{where} 'requester'")
    return ServiceRequest(
        reference=_text(entry, "reference", where=where),
        kind=parse_member(RequestKind, entry.get("kind"), field=f"{where} 'kind'"),
        account=_text(entry, "account", where=where),
        amount=parse_amount(_text(entry, "amount", where=where), field=f"{where} 'amount'"),
        requester=Requester(
            name=_text(requester, "name", where=f"{where} 'requester'"),
            role=_text(requester, "role", where=f"{where} 'requester'"),
            origin=parse_member(
                Origin, requester.get("origin"), field=f"{where} 'requester' 'origin'"
            ),
        ),
    )


def _load_decision(entry: Mapping[str, Any], *, where: str) -> DecisionEntry:
    return DecisionEntry(
        reference=_text(entry, "reference", where=where),
        role=parse_member(Role, entry.get("role"), field=f"{where} 'role'"),
        decision=parse_member(Decision, entry.get("decision"), field=f"{where} 'decision'"),
    )


def load_scenario(path: Path) -> Scenario:
    """Read the ordered list of things that happen."""
    document = _mapping(_read_json(path), where=str(path))
    if "steps" not in document:
        raise InputError(f"{path} is missing the required field 'steps'")
    raw_entries = _sequence(document["steps"], where=f"{path} 'steps'")
    entries: list[ServiceRequest | DecisionEntry] = []
    for position, raw in enumerate(raw_entries):
        where = f"{path} entry #{position + 1}"
        entry = _mapping(raw, where=where)
        action = parse_member(ScenarioAction, entry.get("action"), field=f"{where} 'action'")
        if action is ScenarioAction.INTAKE:
            if "request" not in entry:
                raise InputError(f"{where} is missing the required field 'request'")
            entries.append(_load_request(entry["request"], where=f"{where} 'request'"))
        else:
            entries.append(_load_decision(entry, where=where))
    return Scenario(entries=tuple(entries))
