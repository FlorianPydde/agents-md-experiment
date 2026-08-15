# agents-md-experiment

Measures the minimum AGENTS.md ruleset needed for an LLM to write idiomatic Python.

## Design

Three arms, four models, one session each (n=1).

| Branch | AGENTS.md |
|---|---|
| `arm/control` | none |
| `arm/full` | 24 rules |
| `arm/minimal` | 6 rules |

Models: `claude-opus-5`, `claude-sonnet-5`, `gpt-5.6-sol`, `gpt-5.6-terra`.

Arms live on orphan branches so no session can read another arm's ruleset.
Each session builds the same spec in `experiment/<arm>_<model>/`.
The arm's `AGENTS.md` sits one level above and loads by nested discovery.
Each ruleset opens with a canary token that proves it was loaded.

## Scoring

- Correctness: exact stdout match against the acceptance output.
- Under-application: counts of string-literal comparisons, `dict[str, Any]`, string-key access.
- Adoption: counts of `StrEnum`, `is` comparisons, frozen dataclasses, enum-keyed registries.
- Over-application: three planted traps, plus class count and LOC as a ceremony proxy.

The traps are features that should stay simple: a free-form reference that is only
displayed, a lookup keyed by that reference, and a two-case shipping fee split.
