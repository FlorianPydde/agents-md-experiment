# Governed Service Request Runner

Build a working prototype in this folder.

Work only inside this folder. Do not modify anything outside it.

This is a back office system. It receives service requests from an external intake system,
turns each into a plan of steps, and runs those steps under policy control. Some steps run on
their own. Others must be approved by a named role before they may run. Everything that happens
is written to an append only log.

## Environment

Python 3.12. Use `uv`. Third party packages are allowed.

## Data that arrives from outside

Two files sit in this folder.

`world.json` holds the starting state of the accounts the system acts on. Each account has an
`id`, an `owner`, a `tier` of `standard` or `premium`, a `balance` written as a string to keep
exact decimal amounts, and a `frozen` flag.

`scenario.json` is an ordered list of things that happen. Each entry has an `action` of either
`intake` or `decide`.

An `intake` entry carries a request with:

- `reference` - an identifier such as `REQ-1001`. It appears in reports and is how a caller
  names a request. The system never interprets its contents.
- `kind` - one of `goodwill_credit`, `account_recovery`, `collect_debt`.
- `account` - the account id the request acts on.
- `amount` - a decimal amount written as a string.
- `requester` - `name`, `role`, and `origin`. `origin` is either `internal` or `external`.

A `decide` entry carries a `reference`, a `role`, and a `decision` of `approve` or `reject`.

## Request lifecycle

A request is in exactly one of these states: `received`, `awaiting_approval`, `completed`,
`rejected`, `failed`.

## Plans

The kind of a request determines its ordered steps:

- `goodwill_credit` - `read_account`, `apply_credit`, `notify_customer`
- `account_recovery` - `read_account`, `unfreeze_account`, `apply_credit`, `notify_customer`
- `collect_debt` - `read_account`, `apply_debit`, `notify_customer`

## Supported operations

Six operations exist. Each one has a materiality of `read` or `write`, a check that its
arguments are acceptable before it runs, and the effect it has when it runs. Arguments differ
from one operation to the next and arrive as free form data from the plan.

| Operation | Materiality | Effect |
|---|---|---|
| `read_account` | read | Reports balance, tier and frozen state. Changes nothing. |
| `apply_credit` | write | Increases the balance by the amount. |
| `apply_debit` | write | Decreases the balance by the amount. Fails when the balance is below the amount. |
| `freeze_account` | write | Marks the account frozen. |
| `unfreeze_account` | write | Clears the frozen mark. |
| `notify_customer` | write | Records that the customer was told. Changes no balance. |

Every write operation other than `unfreeze_account` fails when the account is frozen.

`notify_customer` uses one of exactly two fixed message templates, chosen by the `origin` of the
requester: `internal` or `external`. These two are the only ones and are not expected to change.

## Policy

Before a step runs, policy decides whether it may run on its own or needs approval, and if so
by which role. The first matching rule wins:

1. A `read` operation runs on its own.
2. `apply_credit` of 100.00 or less runs on its own.
3. `apply_credit` above 100.00 needs `finance`.
4. `apply_debit` needs `finance`.
5. `freeze_account` and `unfreeze_account` need `risk`.
6. Anything else runs on its own.

The roles that can approve are `finance`, `risk`, and `supervisor`.

## Running a request

Steps run in order.

When a step may run on its own, it runs immediately and the request moves to the next step.

When a step needs approval, the run stops there, the request becomes `awaiting_approval`, and a
pending approval is recorded naming the required role and the step it belongs to.

A later `decide` entry resolves that pending approval. On `approve` the step runs and the
request continues from the next step, which may stop again at another approval. On `reject` the
request becomes `rejected` and no further steps run.

When an operation fails, the request becomes `failed` and no further steps run.

When the last step finishes, the request becomes `completed`.

## Log

Every meaningful occurrence is appended to a log that is never edited or deleted: a request
arriving, a plan being made, a policy decision, an approval being requested, an approval being
resolved, an operation running, an operation failing, and a request reaching a final state.

Each entry records what happened, which request it belongs to, and ordered data about the
occurrence. The log is held in a SQLite database in this folder and survives between runs.

Timestamps must not appear in any command output, so that repeated runs produce identical text.

## Commands

### `run`

```
python -m app run scenario.json --world world.json
```

Starts from a clean database, replays the scenario, and prints a final summary: one line per
request in the order the requests arrived, then one line per account sorted by account id.

A request line is the reference in a 10 character left aligned field, the kind in a 20 character
left aligned field, then the final state.

An account line is the account id in a 10 character left aligned field, the balance right
aligned in a 10 character field with two decimals, two spaces, then `frozen` or `active`.

For the supplied `world.json` and `scenario.json` the exact output is:

```
REQ-1001  goodwill_credit     completed
REQ-1002  goodwill_credit     completed
REQ-1003  account_recovery    rejected
REQ-1004  collect_debt        failed
ACC-100      1500.00  active
ACC-200        50.00  frozen
```

This output is the acceptance criterion and must match exactly.

### `show`

```
python -m app show REQ-1002
```

Prints that request, its steps with their state, its approvals, and its log entries. Looking up
a reference that does not exist exits with a non zero status and a message naming it. A caller
may ask for several references in one run, and asking twice for the same one must not repeat the
underlying lookup work.

### `export`

```
python -m app export --format csv
```

Writes `report.csv`, `report.json` or `report.tsv` holding one record per request with its
reference, kind, final state, account, and amount. More formats are expected to be added later.

### `serve`

```
python -m app serve
```

Serves an HTTP API offering at least:

- health
- list requests, and fetch one request by reference
- list the pending approvals
- resolve a pending approval by supplying a role and a decision
- fetch the log entries for a request
- list the supported operations and the policy rules

## Errors

The program exits with a non zero status and a clear message, never a traceback, when a file is
missing or malformed, when a value is outside the sets listed above, when an amount is negative,
when a required field is absent, when a decision names a request with nothing pending, or when a
decision comes from a role that is not the one required.

## Deliverables

- A working program satisfying the commands above.
- Automated tests covering the policy rules, the approval flow, and at least one failing
  operation.
- A short `README.md` in this folder saying how to run it and how the pieces fit together.

Nothing else is prescribed. All design decisions are yours.

