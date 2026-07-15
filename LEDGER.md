# Line-count ledger

Budget: ≤ ~600 bespoke script/YAML lines across the whole project. Append per PRD; never rewrite past rows.

Scope: this ledger counts **shipmate's own** bespoke actions/scripts only. Sample-repo (`repo-example-*`) fixture content — Terramate `script`/`generate_hcl` blocks and helper scripts like `tools/mutate-state.ps1` — is test-fixture configuration, not shipmate tooling, and is intentionally excluded from the budget.

| PRD | Component | Lines | Notes |
|-----|-----------|-------|-------|
| 0 | actions/state/action.yml | 33 | cache-based state restore/save |
