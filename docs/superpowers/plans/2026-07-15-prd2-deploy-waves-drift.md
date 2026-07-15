# PRD 2: Deploy Waves + Drift — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add exact-plan wave applies (`deploy.yml`) and nightly drift detection (`drift.yml`) to shipmate — the serverless-Terrateam apply half of the engine.

**Architecture:** Plan→store→review→apply. `plan-cell` (PRD 1) stores a reviewed `.otplan` artifact + opens a *pending* apply check. `apply-cell` (new) applies that **exact** stored plan (never re-plans; stale → fail-safe), then completes the check. `deploy-detect` (new) maps a merge commit → its PR, filters to still-pending applies, and orders them into topological **waves** (`scripts/waves`). `drift-cell` (new) plans all stacks nightly and files/auto-closes GitHub Issues. A shared `scripts/plan-classify` helper (change classification + fingerprint) is used by plan-cell, apply-cell, and drift-cell to stay under the line budget.

**Tech Stack:** GitHub Actions composite actions (`bash` + `python3`), Terramate CLI (`experimental run-graph`, `script run`), OpenTofu, `gh` CLI, python stdlib `graphlib`. Sample repos = the E2E test harness (no separate unit runner for actions; python scripts are pytest-unit-tested).

## Global Constraints

Copied verbatim from `shipmate/CONTRACT.md`, `shipmate/CLAUDE.md`, and the PRD-2 design spec. **Every task's requirements implicitly include this section.**

- **Check names, verbatim:** `plan / <env> / <stack>`, `apply / <env> / <stack>`, aggregate `shipmate / checkmate`. `<env>`/`<stack>` substituted with the actual env name and stack value passed to the cell.
- **Exact-plan invariant:** apply the reviewed `.otplan` artifact from the plan run; **never silently re-plan**. Stale plan/state → fail safe ("saved plan is stale"), instruct re-plan. Fail-safe on missing/mismatched plan — never fall back to re-plan.
- **TF_VAR fingerprint:** sha256 over `TF_VAR_*` values **plus `TF_WORKSPACE` when set**, sorted name→value JSON. Excludes ephemeral credential env vars (`AWS_*` etc.). plan-cell and apply-cell MUST use the byte-identical algorithm. Mismatch → fail with a diff of variable **names**, never values.
- **Security (GHA injection):** never interpolate author-controlled `${{ inputs.* }}`/`${{ matrix.* }}` into `run:` bash — pass via step `env:` and reference `"$VAR"`. Build JSON bodies (check bodies, issue bodies) in python from env vars, never shell heredocs with interpolation.
- **action.yml manifest:** no `${{ }}` in input `description:` fields (GHA evaluates at load → error). Flow-style `{ description: ..., required: true }` must quote any value containing `,` or `:`.
- **No env names in workflow YAML, ever.** Envs come from stack `env/<name>` tags. Plan runs against `<env>`, apply runs against `<env>-apply`.
- **Ordering from `run-graph`, never `list`.** `list` dep filters track only data deps, not `after`/`before`. Wave computation: topological levels over the **full** graph, then filter each level to the work set (naive edges∩changed loses transitive order). Empty middle waves are normal.
- **Skip-propagation guard** on every wave job: `if: ${{ !failure() && !cancelled() && needs.detect.outputs.waveN != '[]' }}`.
- **Matrix ≤ 256 cells**; waves ≤ 8 levels (`>8` → fail "split the PR").
- **Consumption:** consumer repos pin shipmate actions by **commit SHA**. During dev, pin sample workflows to this branch's HEAD SHA; finalize to the merge SHA at the end.
- **Per-flavor env injection:** stacks → `TF_VAR_env`+`TF_VAR_region`; workspaces → `TF_WORKSPACE`; folders → none. Uniform workflow; empty resolves are harmless.
- **State path per flavor:** stacks `${{ matrix.stack }}/.state` (stack-relative — PRD-1 carryover fix), folders `<leaf>/terraform.tfstate`, workspaces `terraform.tfstate.d`.
- **Line budget ≤ ~600 bespoke shipmate script/action lines.** Running 374. Sample-repo workflow YAML is excluded (fixture). Append to `LEDGER.md`, never rewrite past rows. If PRD 2 pushes over ~600 → surface as a decision-gate flag, do not silently exceed.
- **`stacks/auth` has a Terramate `script` subtree override** — always apply/plan via `terramate script run`, never raw `tofu`, so overrides (its `tofu validate` gate) are honored.
- **Windows dev toolchain:** terramate/tofu run locally per the shipmate-windows-toolchain notes (PATH trick; watch BOM/CRLF; `.state`/`terraform.tfstate.d` are gitignored). Python scripts are extensionless — load in tests via `SourceFileLoader` (see existing `scripts/tests/test_build_matrix.py`).

## File Structure

**New shipmate code (counts toward budget):**
- `scripts/plan-classify` — shared: classify plan.json changes + compute fingerprint. Pure core unit-tested.
- `scripts/waves` — parse run-graph dot → topological levels → assign stack×env cells to `wave0..wave7`. Pure core unit-tested.
- `scripts/deploy-detect` — orchestration: merge SHA → PR head SHA, work set (changed stacks minus already-completed applies), invoke waves. Imports `build-matrix` + `waves` pure functions.
- `actions/deploy-detect/action.yml` — thin composite wrapper running `scripts/deploy-detect`.
- `actions/apply-cell/action.yml` — exact-plan apply composite (shared with PRD 3).
- `actions/drift-cell/action.yml` — nightly drift plan + issue upsert composite.

**Modified shipmate code:**
- `actions/plan-cell/action.yml` — replace inline classify/fingerprint python with a call to `scripts/plan-classify` (behavior-preserving except fingerprint now includes `TF_WORKSPACE`).
- `scripts/build-matrix` — add `all-stacks` mode (enumerate all stacks, for drift).
- `actions/build-matrix/action.yml` — expose the `all-stacks` input.
- `CONTRACT.md` — fingerprint definition gains `TF_WORKSPACE`.
- `LEDGER.md` — PRD-2 rows.

**Tests (excluded from budget):**
- `scripts/tests/test_plan_classify.py`, `scripts/tests/test_waves.py`, `scripts/tests/test_deploy_detect.py`, `scripts/tests/test_build_matrix.py` (extend).
- `scripts/tests/fixtures/run-graph-stacks.dot` — real captured run-graph output.

**Sample-repo fixture (excluded from budget), authored in all 3 `repo-example-*`:**
- `.github/workflows/deploy.yml`, `.github/workflows/drift.yml`.

---

### Task 1: `scripts/plan-classify` — shared classify + fingerprint helper

Extracts the plan-classification and fingerprint logic currently inlined in `plan-cell` so apply-cell and drift-cell reuse it (biggest budget saving) and the fingerprint has one definition. Adds `TF_WORKSPACE` to the fingerprint **only when set** (keeps stacks/folders fingerprints byte-identical to PRD 1; disambiguates workspaces per-env).

**Files:**
- Create: `scripts/plan-classify`
- Test: `scripts/tests/test_plan_classify.py`

**Interfaces:**
- Produces:
  - `classify(plan: dict) -> dict` returning `{"changed": bool, "add": int, "change": int, "destroy": int}`.
  - `fingerprint(environ: Mapping[str,str]) -> str` (64-hex sha256).
  - CLI: `python3 scripts/plan-classify [PLAN_JSON]` (default `plan.json`) writes `changed/add/change/destroy` to `$GITHUB_OUTPUT` and `fingerprint.txt` in cwd. `python3 scripts/plan-classify --fingerprint-only` writes only `fingerprint.txt` (no plan.json read) — used by apply-cell to recompute the current fingerprint.

- [ ] **Step 1: Write the failing tests**

```python
# scripts/tests/test_plan_classify.py
import importlib.util, pathlib
from importlib.machinery import SourceFileLoader

_p = pathlib.Path(__file__).resolve().parents[1] / "plan-classify"
_loader = SourceFileLoader("plan_classify", str(_p))
_spec = importlib.util.spec_from_loader("plan_classify", _loader)
pc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pc)


def _rc(*actions):
    return {"change": {"actions": list(actions)}}


def test_classify_counts_add_change_destroy():
    plan = {"resource_changes": [
        _rc("create"), _rc("update"), _rc("delete"),
        _rc("no-op"), _rc("read"),
    ]}
    assert pc.classify(plan) == {"changed": True, "add": 1, "change": 1, "destroy": 1}


def test_classify_no_op_and_read_are_not_changes():
    plan = {"resource_changes": [_rc("no-op"), _rc("read")]}
    assert pc.classify(plan) == {"changed": False, "add": 0, "change": 0, "destroy": 0}


def test_classify_output_only_change_is_a_change():
    plan = {"resource_changes": [], "output_changes": {"name": {"actions": ["update"]}}}
    assert pc.classify(plan)["changed"] is True


def test_classify_empty_plan():
    assert pc.classify({}) == {"changed": False, "add": 0, "change": 0, "destroy": 0}


def test_fingerprint_excludes_non_tfvar_and_is_sorted_deterministic():
    a = pc.fingerprint({"TF_VAR_env": "dev-eu", "TF_VAR_region": "eu", "AWS_SECRET": "x", "PATH": "/"})
    b = pc.fingerprint({"TF_VAR_region": "eu", "TF_VAR_env": "dev-eu"})
    assert a == b and len(a) == 64


def test_fingerprint_includes_tf_workspace_only_when_set():
    with_ws = pc.fingerprint({"TF_WORKSPACE": "dev-us"})
    with_ws2 = pc.fingerprint({"TF_WORKSPACE": "dev-eu"})
    empty = pc.fingerprint({})
    empty_blank_ws = pc.fingerprint({"TF_WORKSPACE": ""})
    assert with_ws != with_ws2          # env identity disambiguates workspaces
    assert empty == empty_blank_ws      # unset/blank == excluded (stacks/folders unchanged)


def test_fingerprint_stacks_flavor_matches_tfvar_only_algo():
    # PRD-1 algo was sorted TF_VAR_* name->value JSON; TF_WORKSPACE unset must not change it.
    import json, hashlib
    env = {"TF_VAR_env": "dev-eu", "TF_VAR_region": "eu-west-1"}
    prd1 = hashlib.sha256(json.dumps(dict(sorted(env.items())), sort_keys=True).encode()).hexdigest()
    assert pc.fingerprint(env) == prd1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest scripts/tests/test_plan_classify.py -v`
Expected: FAIL — `No module named` / `AttributeError: module 'plan_classify' has no attribute 'classify'`.

- [ ] **Step 3: Write the implementation**

```python
#!/usr/bin/env python3
"""Shared plan classification + TF_VAR/TF_WORKSPACE fingerprint.

Used by plan-cell (PRD 1), apply-cell, and drift-cell (PRD 2) so change
classification and the apply-match fingerprint have exactly one definition.
"""
import hashlib
import json
import os
import sys


def classify(plan):
    """Classify an OpenTofu `show -json` plan dict into change counts."""
    rcs = plan.get("resource_changes") or []
    # "no-op" = unchanged; "read" = deferred data-source read (not a real change).
    changed = [r for r in rcs if any(a not in ("no-op", "read") for a in r["change"]["actions"])]
    oc = plan.get("output_changes") or {}
    out_changed = any(c.get("actions", ["no-op"]) != ["no-op"] for c in oc.values())
    add = sum(1 for r in changed if "create" in r["change"]["actions"])
    destroy = sum(1 for r in changed if "delete" in r["change"]["actions"])
    change = sum(1 for r in changed if set(r["change"]["actions"]) == {"update"})
    return {"changed": bool(changed) or out_changed, "add": add, "change": change, "destroy": destroy}


def fingerprint(environ):
    """sha256 over sorted TF_VAR_* (name->value) plus TF_WORKSPACE when set.

    TF_WORKSPACE is env identity for the workspaces flavor and is NOT a TF_VAR_*,
    so without it two envs of a workspaces stack hash identically. It is included
    only when non-empty, so stacks/folders fingerprints match the PRD-1 algo.
    """
    payload = {k: v for k, v in environ.items() if k.startswith("TF_VAR_")}
    ws = environ.get("TF_WORKSPACE")
    if ws:
        payload["TF_WORKSPACE"] = ws
    return hashlib.sha256(json.dumps(dict(sorted(payload.items())), sort_keys=True).encode()).hexdigest()


def main(argv):
    open("fingerprint.txt", "w", encoding="utf-8").write(fingerprint(os.environ))
    if "--fingerprint-only" in argv:
        return
    plan_json = next((a for a in argv[1:] if not a.startswith("--")), "plan.json")
    counts = classify(json.load(open(plan_json, encoding="utf-8")))
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as fh:
        fh.write(f"changed={'true' if counts['changed'] else 'false'}\n")
        fh.write(f"add={counts['add']}\nchange={counts['change']}\ndestroy={counts['destroy']}\n")


if __name__ == "__main__":
    main(sys.argv)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest scripts/tests/test_plan_classify.py -v`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add scripts/plan-classify scripts/tests/test_plan_classify.py
git commit -m "scripts/plan-classify: shared plan classify + TF_WORKSPACE-aware fingerprint"
```

---

### Task 2: Refactor `plan-cell` onto the shared helper + CONTRACT fingerprint update

Behavior-preserving except the fingerprint now includes `TF_WORKSPACE` (workspaces flavor). Removes the inline classify/fingerprint python from `plan-cell`, calling `scripts/plan-classify` instead.

**Files:**
- Modify: `actions/plan-cell/action.yml` (the "Render + classify plan" step, lines ~28-58)
- Modify: `CONTRACT.md` (OpenTofu note / fingerprint area — add fingerprint definition)

**Interfaces:**
- Consumes: `scripts/plan-classify` (Task 1) via `$GITHUB_ACTION_PATH/../../scripts/plan-classify`.
- Produces: unchanged step outputs `changed/add/change/destroy` and `fingerprint.txt`.

- [ ] **Step 1: Replace the "Render + classify plan" step body**

In `actions/plan-cell/action.yml`, replace the step that currently inlines the classify+fingerprint python (the `python3 - <<'PY' ... PY` heredoc after `tofu ... show -json ... > plan.json`) with:

```yaml
    - name: Render + classify plan
      id: plan
      shell: bash
      env:
        STACK: ${{ inputs.stack }}
      run: |
        set -euo pipefail
        tofu -chdir="$STACK" show -no-color stack.otplan > plan.txt
        tofu -chdir="$STACK" show -json stack.otplan > plan.json
        python3 "$GITHUB_ACTION_PATH/../../scripts/plan-classify" plan.json
```

(`plan-classify` writes `changed/add/change/destroy` to `$GITHUB_OUTPUT` and `fingerprint.txt` to cwd — same outputs the downstream steps already consume.)

- [ ] **Step 2: Verify plan-cell still references fingerprint.txt + outputs**

Run: `grep -n "fingerprint.txt\|steps.plan.outputs" actions/plan-cell/action.yml`
Expected: the "Upload plan artifact" step still lists `fingerprint.txt`; the apply-check step still reads `steps.plan.outputs.changed` and `fingerprint.txt`. No inline python remains in the classify step.

- [ ] **Step 3: Local smoke — run plan-classify against a real plan.json**

In `../repo-example-stacks` (with `TF_VAR_env`/`TF_VAR_region` set per the Windows toolchain notes):
```bash
cd stacks/dns && tofu init -input=false && tofu plan -input=false -lock=false -out=stack.otplan && tofu show -json stack.otplan > plan.json
GITHUB_OUTPUT=/dev/stdout TF_VAR_env=dev-eu TF_VAR_region=eu-west-1 python3 ../../../shipmate/scripts/plan-classify plan.json
```
Expected: prints `changed=true`, `add=`/`change=`/`destroy=` counts; a 64-hex `fingerprint.txt` is written. (Clean up `plan.json`/`stack.otplan`/`.state` after.)

- [ ] **Step 4: Update CONTRACT.md fingerprint definition**

Add to `CONTRACT.md` (near the OpenTofu note), verbatim:

```markdown
## Apply-match fingerprint

Each plan stores a fingerprint (`fingerprint.txt`, artifact `external_id` on the
apply check): `sha256` over the sorted JSON of every `TF_VAR_*` environment
variable (name→value) **plus `TF_WORKSPACE` when it is set**. Ephemeral
credential vars (`AWS_*`, etc.) are excluded. `TF_WORKSPACE` is included because
it is the workspaces-flavor env identity and is not a `TF_VAR_*`; without it two
environments of a workspaces stack would fingerprint identically and an apply
could match the wrong environment's reviewed plan. plan-cell and apply-cell use
a byte-identical algorithm (`scripts/plan-classify`). On mismatch, apply fails
safe and reports differing variable **names** only — never values.
```

- [ ] **Step 5: Commit**

```bash
git add actions/plan-cell/action.yml CONTRACT.md
git commit -m "plan-cell: use scripts/plan-classify; CONTRACT: TF_WORKSPACE in fingerprint"
```

---

### Task 3: `scripts/waves` — topological wave computation

**Files:**
- Create: `scripts/waves`
- Test: `scripts/tests/test_waves.py`
- Create: `scripts/tests/fixtures/run-graph-stacks.dot` (captured real output)

**Interfaces:**
- Produces:
  - `parse_dot(text: str) -> dict[str, set[str]]` — maps each stack node to the set of stacks it depends on (must run *before* it).
  - `levels(deps: dict[str, set[str]]) -> list[list[str]]` — topological levels over the full graph; level 0 = no dependencies.
  - `assign_waves(levels: list[list[str]], cells: list[dict]) -> list[list[dict]]` — bucket each `{stack, environment, workload}` cell into the wave index of its stack's level; drops levels with no cells but preserves index order (a cell's wave = its stack's full-graph level, so empty middle waves are normal). Returns list indexed by wave.
  - CLI: reads dot on **stdin**, work-set cells from env `SHIPMATE_WORKSET` (JSON array), writes `wave0..wave7` (JSON arrays) + `empty` to `$GITHUB_OUTPUT`. `--reverse` reverses wave order (destroy). Errors if any cell's stack maps to level > 7.

- [ ] **Step 1: Capture the real run-graph fixture**

In `../repo-example-stacks`:
```bash
terramate experimental run-graph > ../../shipmate/scripts/tests/fixtures/run-graph-stacks.dot
```
Open the file; confirm edge direction (an edge `"A" -> "B"` means A runs before B, i.e. B depends on A). **If terramate emits the reverse direction, invert the `deps` mapping in `parse_dot` accordingly** — the test below encodes the expected fixture DAG `dns → platform → {auth, workers} → app → {tenant-a, tenant-b}`, so make `parse_dot` produce that.

- [ ] **Step 2: Write the failing tests**

```python
# scripts/tests/test_waves.py
import importlib.util, pathlib
from importlib.machinery import SourceFileLoader

_dir = pathlib.Path(__file__).resolve().parents[1]
_loader = SourceFileLoader("waves", str(_dir / "waves"))
_spec = importlib.util.spec_from_loader("waves", _loader)
w = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(w)

FIXTURE = (_dir / "tests" / "fixtures" / "run-graph-stacks.dot").read_text()


def test_parse_dot_matches_fixture_dag():
    deps = w.parse_dot(FIXTURE)
    assert deps["stacks/platform"] == {"stacks/dns"}
    assert deps["stacks/app"] == {"stacks/auth", "stacks/workers"}
    assert deps["stacks/dns"] == set()


def test_levels_are_topological():
    deps = w.parse_dot(FIXTURE)
    lv = w.levels(deps)
    assert lv[0] == ["stacks/dns"]
    assert lv[1] == ["stacks/platform"]
    assert set(lv[2]) == {"stacks/auth", "stacks/workers"}
    assert lv[3] == ["stacks/app"]
    assert set(lv[4]) == {"stacks/tenant-a", "stacks/tenant-b"}


def test_assign_waves_preserves_transitive_order_with_empty_middle():
    # Only dns (level 0) and app (level 3) in the work set -> empty waves 1,2.
    deps = w.parse_dot(FIXTURE)
    lv = w.levels(deps)
    cells = [
        {"stack": "stacks/dns", "environment": "dev-us", "workload": "net"},
        {"stack": "stacks/app", "environment": "dev-eu", "workload": "app"},
    ]
    waves = w.assign_waves(lv, cells)
    assert waves[0] == [{"stack": "stacks/dns", "environment": "dev-us", "workload": "net"}]
    assert waves[1] == [] and waves[2] == []
    assert waves[3] == [{"stack": "stacks/app", "environment": "dev-eu", "workload": "app"}]


def test_assign_waves_cross_env_edge_same_wave_index():
    # dns@dev-us must be an earlier wave than platform@dev-eu (cross-env edge).
    deps = w.parse_dot(FIXTURE)
    lv = w.levels(deps)
    cells = [
        {"stack": "stacks/platform", "environment": "dev-eu", "workload": ""},
        {"stack": "stacks/dns", "environment": "dev-us", "workload": ""},
    ]
    waves = w.assign_waves(lv, cells)
    assert waves[0][0]["stack"] == "stacks/dns"
    assert waves[1][0]["stack"] == "stacks/platform"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest scripts/tests/test_waves.py -v`
Expected: FAIL — module has no attribute `parse_dot`.

- [ ] **Step 4: Write the implementation**

```python
#!/usr/bin/env python3
"""Compute apply waves = topological levels of the Terramate `after` DAG.

Reads `terramate experimental run-graph` dot output on stdin and a work-set of
{stack, environment, workload} cells from SHIPMATE_WORKSET (JSON). Emits
wave0..wave7 cell arrays. Ordering is over the FULL graph, then filtered to the
work set, so transitive order through unchanged stacks is preserved and empty
middle waves are normal.
"""
import json
import os
import re
import sys
from graphlib import CycleError, TopologicalSorter

MAX_WAVES = 8
_EDGE = re.compile(r'"([^"]+)"\s*->\s*"([^"]+)"')


def parse_dot(text):
    """Return {node: set(dependencies-that-run-before-it)} from run-graph dot.

    An edge `"A" -> "B"` in terramate run-graph means A runs before B, so B
    depends on A. Every node (including leaves/roots) appears as a key.
    """
    deps = {}
    for line in text.splitlines():
        m = _EDGE.search(line)
        if not m:
            continue
        before, after = m.group(1), m.group(2)
        deps.setdefault(before, set())
        deps.setdefault(after, set()).add(before)
    return deps


def levels(deps):
    """Topological levels; level 0 = nodes with no dependencies."""
    ts = TopologicalSorter(deps)
    ts.prepare()
    out = []
    while ts.is_active():
        ready = sorted(ts.get_ready())
        out.append(ready)
        for n in ready:
            ts.done(n)
    return out


def assign_waves(levels_, cells):
    """Bucket cells by the full-graph level of their stack (index-preserving)."""
    level_of = {stack: i for i, lv in enumerate(levels_) for stack in lv}
    if not levels_:
        return []
    waves = [[] for _ in range(len(levels_))]
    for c in cells:
        waves[level_of[c["stack"]]].append(c)
    return waves


def main(argv):
    deps = parse_dot(sys.stdin.read())
    try:
        lv = levels(deps)
    except CycleError as e:
        raise SystemExit(f"::error::dependency cycle in run-graph: {e}")
    cells = json.loads(os.environ.get("SHIPMATE_WORKSET", "[]"))
    waves = assign_waves(lv, cells)
    # Trim trailing empty waves but keep interior ones (index == topological level).
    used = [w for w in waves if w]
    if len(waves) > MAX_WAVES:
        # Only fail if a *populated* wave exceeds the pre-declared job count.
        max_used = max((i for i, w in enumerate(waves) if w), default=-1)
        if max_used >= MAX_WAVES:
            raise SystemExit(
                f"::error::change spans {max_used + 1} dependency levels (> {MAX_WAVES}); split the PR."
            )
    if "--reverse" in argv:
        waves = list(reversed(waves))
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as fh:
        for i in range(MAX_WAVES):
            wave = waves[i] if i < len(waves) else []
            fh.write(f"wave{i}={json.dumps(wave)}\n")
        fh.write(f"empty={'true' if not used else 'false'}\n")


if __name__ == "__main__":
    main(sys.argv)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest scripts/tests/test_waves.py -v`
Expected: PASS (4 passed). If parse_dot direction was inverted in Step 1, the fixture-based tests confirm correctness.

- [ ] **Step 6: Commit**

```bash
git add scripts/waves scripts/tests/test_waves.py scripts/tests/fixtures/run-graph-stacks.dot
git commit -m "scripts/waves: topological wave computation from run-graph dot"
```

---

### Task 4: `build-matrix` refactor → `compute_cells` + `all-stacks` mode

Extracts the changed→cells fan-out into a reusable `compute_cells()` (consumed by `deploy-detect` in Task 5) and adds an `all-stacks` mode that enumerates every stack (consumed by `drift.yml` in Task 9). Behavior-preserving for the existing preview path.

**Files:**
- Modify: `scripts/build-matrix` (extract `compute_cells`, add `_list_stacks`, read `SHIPMATE_ALL_STACKS`)
- Modify: `actions/build-matrix/action.yml` (add `all-stacks` input → `SHIPMATE_ALL_STACKS` env)
- Test: `scripts/tests/test_build_matrix.py` (extend)

**Interfaces:**
- Produces: `compute_cells(all_stacks: bool = False, base: str = "") -> list[dict]` — the fan-out (`{stack, environment, workload}`), untagged guard, and 256-cell guard, from either `terramate list` (all) or `terramate list --changed -B base`.
- Consumes: nothing new.

- [ ] **Step 1: Write the failing tests (extend the existing file)**

Append to `scripts/tests/test_build_matrix.py`:

```python
def test_list_stacks_changed_uses_changed_flag(monkeypatch):
    captured = {}
    monkeypatch.setattr(bm, "_run", lambda args: captured.setdefault("args", args) or "stacks/a\n")
    assert bm._list_stacks(all_stacks=False, base="deadbeef") == ["stacks/a"]
    assert captured["args"] == ["terramate", "list", "--changed", "-B", "deadbeef"]


def test_list_stacks_all_omits_changed_flag(monkeypatch):
    captured = {}
    monkeypatch.setattr(bm, "_run", lambda args: captured.setdefault("args", args) or "stacks/a\nstacks/b\n")
    assert bm._list_stacks(all_stacks=True, base="") == ["stacks/a", "stacks/b"]
    assert captured["args"] == ["terramate", "list"]


def test_compute_cells_fans_out_and_guards_untagged(monkeypatch):
    monkeypatch.setattr(bm, "_list_stacks", lambda all_stacks, base: ["stacks/app"])
    monkeypatch.setattr(bm, "_tags", lambda s: ["env/dev-eu", "env/dev-us", "workload/app"])
    cells = bm.compute_cells(all_stacks=True)
    assert cells == [
        {"stack": "stacks/app", "environment": "dev-eu", "workload": "app"},
        {"stack": "stacks/app", "environment": "dev-us", "workload": "app"},
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest scripts/tests/test_build_matrix.py -v`
Expected: FAIL — `module 'build_matrix' has no attribute '_list_stacks'` / `compute_cells`.

- [ ] **Step 3: Refactor `scripts/build-matrix`**

Replace `_changed_stacks` and `main` with (keep `build_matrix`, `_run`, `_tags`, `MatrixTooLarge` unchanged):

```python
def _list_stacks(all_stacks, base):
    args = ["terramate", "list"]
    if not all_stacks:
        args.append("--changed")
        if base:
            args += ["-B", base]
    return [s.strip() for s in _run(args).splitlines() if s.strip()]


def compute_cells(all_stacks=False, base=""):
    stacks = _list_stacks(all_stacks, base)
    tags_by_stack = {s: _tags(s) for s in stacks}
    # A stack with no env/* tag would contribute no cells and vanish from
    # preview/apply/drift — fail loud instead of silently skipping it.
    untagged = [s for s in stacks
                if not any(t.startswith("env/") for t in tags_by_stack[s])]
    if untagged:
        raise SystemExit(
            "::error::stack(s) have no env/* tag and cannot fan out to any "
            "environment (they would silently skip): " + ", ".join(sorted(untagged))
        )
    stacks_by_env = {}
    for s in stacks:
        for t in tags_by_stack[s]:
            if t.startswith("env/"):
                stacks_by_env.setdefault(t[len("env/"):], []).append(s)
    return build_matrix(sorted(stacks_by_env), stacks_by_env, tags_by_stack)


def main():
    all_stacks = os.environ.get("SHIPMATE_ALL_STACKS", "") == "true"
    base = os.environ.get("SHIPMATE_BASE_SHA", "")
    cells = compute_cells(all_stacks, base)
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as fh:
        fh.write(f"matrix={json.dumps({'include': cells})}\n")
        fh.write(f"empty={'true' if not cells else 'false'}\n")
        fh.write(f"count={len(cells)}\n")
    listing = ", ".join(f"{c['environment']}/{c['stack']}" for c in cells) or "(none)"
    print(f"{len(cells)} cell(s): {listing}")
```

- [ ] **Step 4: Add the `all-stacks` input to the wrapper action**

In `actions/build-matrix/action.yml`, add under `inputs:`:
```yaml
  all-stacks:
    description: When 'true', enumerate every stack (drift) instead of only changed.
    required: false
    default: "false"
```
and add to the `build` step's `env:` block:
```yaml
        SHIPMATE_ALL_STACKS: ${{ inputs.all-stacks }}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest scripts/tests/test_build_matrix.py -v`
Expected: PASS (existing 3 + new 3 = 6 passed).

- [ ] **Step 6: Commit**

```bash
git add scripts/build-matrix actions/build-matrix/action.yml scripts/tests/test_build_matrix.py
git commit -m "build-matrix: extract compute_cells + add all-stacks mode (drift)"
```

---

### Task 5: `scripts/deploy-detect` + `actions/deploy-detect`

Orchestrates the deploy detect job: merge commit → PR head SHA, work set = changed stacks minus already-completed applies, waves over the remainder, plus the preview run id for artifact download.

**Files:**
- Create: `scripts/deploy-detect`
- Create: `actions/deploy-detect/action.yml`
- Test: `scripts/tests/test_deploy_detect.py`

**Interfaces:**
- Consumes: `build-matrix` (`compute_cells`, Task 4), `waves` (`parse_dot`, `levels`, `assign_waves`, `MAX_WAVES`, Task 3) via runtime `SourceFileLoader`.
- Produces:
  - `filter_pending(cells, completed_names: set[str]) -> list[dict]` — drops cells whose `apply / <env> / <stack>` check is already completed.
  - CLI → `$GITHUB_OUTPUT`: `wave0..wave7` (JSON), `head-sha`, `preview-run-id`, `empty`.
  - Action `actions/deploy-detect` outputs: `wave0..wave7`, `head-sha`, `preview-run-id`, `empty`.

- [ ] **Step 1: Write the failing test**

```python
# scripts/tests/test_deploy_detect.py
import importlib.util, pathlib
from importlib.machinery import SourceFileLoader

_p = pathlib.Path(__file__).resolve().parents[1] / "deploy-detect"
_loader = SourceFileLoader("deploy_detect", str(_p))
_spec = importlib.util.spec_from_loader("deploy_detect", _loader)
dd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dd)


def test_filter_pending_drops_completed_applies():
    cells = [
        {"stack": "stacks/dns", "environment": "dev-eu", "workload": ""},
        {"stack": "stacks/app", "environment": "dev-eu", "workload": ""},
    ]
    completed = {"apply / dev-eu / stacks/dns"}   # applied pre-merge -> skip
    assert dd.filter_pending(cells, completed) == [
        {"stack": "stacks/app", "environment": "dev-eu", "workload": ""},
    ]


def test_filter_pending_keeps_all_when_none_completed():
    cells = [{"stack": "stacks/dns", "environment": "dev-eu", "workload": ""}]
    assert dd.filter_pending(cells, set()) == cells
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest scripts/tests/test_deploy_detect.py -v`
Expected: FAIL — module has no attribute `filter_pending`.

- [ ] **Step 3: Write `scripts/deploy-detect`**

```python
#!/usr/bin/env python3
"""deploy.yml detect: merge commit -> PR head SHA -> pending applies -> waves.

Exact-plan / fail-safe model: the work set is the merged PR's changed stacks
whose apply check is still pending (completed = applied pre-merge via PRD 3, or
no-change). No 'last deployed ref' diff — pending checks ARE the queue.
"""
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
from importlib.machinery import SourceFileLoader

_D = pathlib.Path(__file__).resolve().parent


def _load(fname):
    loader = SourceFileLoader(fname.replace("-", "_"), str(_D / fname))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


bm = _load("build-matrix")
wv = _load("waves")


def filter_pending(cells, completed_names):
    return [c for c in cells
            if f"apply / {c['environment']} / {c['stack']}" not in completed_names]


def _gh_json(path):
    p = subprocess.run(["gh", "api", path], capture_output=True, text=True)
    if p.returncode != 0:
        sys.stderr.write(p.stderr)
        raise SystemExit(f"::error::gh api failed: {path}")
    return json.loads(p.stdout)


def main():
    repo = os.environ["GITHUB_REPOSITORY"]
    merge_sha = os.environ["GITHUB_SHA"]
    pulls = _gh_json(f"repos/{repo}/commits/{merge_sha}/pulls")
    # squash merges drop the PR head SHA from main -> map via the commit's PRs.
    head = pulls[0]["head"]["sha"] if pulls else merge_sha
    cells = bm.compute_cells(all_stacks=False, base=os.environ.get("SHIPMATE_BASE_SHA", ""))
    checks = _gh_json(f"repos/{repo}/commits/{head}/check-runs?per_page=100").get("check_runs", [])
    completed = {c["name"] for c in checks
                 if c["name"].startswith("apply / ") and c["status"] == "completed"}
    pending = filter_pending(cells, completed)
    graph = subprocess.run(["terramate", "experimental", "run-graph"],
                           capture_output=True, text=True)
    if graph.returncode != 0:
        sys.stderr.write(graph.stderr)
        raise SystemExit("::error::run-graph failed")
    waves = wv.assign_waves(wv.levels(wv.parse_dot(graph.stdout)), pending)
    if any(i >= wv.MAX_WAVES for i, w in enumerate(waves) if w):
        raise SystemExit(f"::error::change spans > {wv.MAX_WAVES} dependency levels; split the PR.")
    runs = _gh_json(
        f"repos/{repo}/actions/workflows/preview.yml/runs?head_sha={head}&status=success"
    ).get("workflow_runs", [])
    preview_run = runs[0]["id"] if runs else ""
    used = [w for w in waves if w]
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as fh:
        for i in range(wv.MAX_WAVES):
            fh.write(f"wave{i}={json.dumps(waves[i] if i < len(waves) else [])}\n")
        fh.write(f"head-sha={head}\n")
        fh.write(f"preview-run-id={preview_run}\n")
        fh.write(f"empty={'true' if not used else 'false'}\n")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Write `actions/deploy-detect/action.yml`**

```yaml
name: shipmate deploy-detect
description: Map a merge commit to its PR, find still-pending applies, order them into waves.
inputs:
  base-sha:
    description: Previous main tip for change detection (github.event.before).
    required: true
  github-token:
    description: GITHUB_TOKEN with contents/checks/pull-requests/actions read.
    required: true
outputs:
  wave0: { description: JSON cell array for wave 0., value: "${{ steps.d.outputs.wave0 }}" }
  wave1: { description: JSON cell array for wave 1., value: "${{ steps.d.outputs.wave1 }}" }
  wave2: { description: JSON cell array for wave 2., value: "${{ steps.d.outputs.wave2 }}" }
  wave3: { description: JSON cell array for wave 3., value: "${{ steps.d.outputs.wave3 }}" }
  wave4: { description: JSON cell array for wave 4., value: "${{ steps.d.outputs.wave4 }}" }
  wave5: { description: JSON cell array for wave 5., value: "${{ steps.d.outputs.wave5 }}" }
  wave6: { description: JSON cell array for wave 6., value: "${{ steps.d.outputs.wave6 }}" }
  wave7: { description: JSON cell array for wave 7., value: "${{ steps.d.outputs.wave7 }}" }
  head-sha: { description: PR head SHA for the merged commit., value: "${{ steps.d.outputs.head-sha }}" }
  preview-run-id: { description: Preview workflow run id holding the reviewed plan artifacts., value: "${{ steps.d.outputs.preview-run-id }}" }
  empty: { description: "'true' when no wave has any cell.", value: "${{ steps.d.outputs.empty }}" }
runs:
  using: composite
  steps:
    - id: d
      shell: bash
      env:
        GH_TOKEN: ${{ inputs.github-token }}
        SHIPMATE_BASE_SHA: ${{ inputs.base-sha }}
      run: python3 "$GITHUB_ACTION_PATH/../../scripts/deploy-detect"
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest scripts/tests/test_deploy_detect.py -v`
Expected: PASS (2 passed). (Import triggers loading sibling `build-matrix`/`waves` — confirms the loader wiring.)

- [ ] **Step 6: Commit**

```bash
git add scripts/deploy-detect actions/deploy-detect/action.yml scripts/tests/test_deploy_detect.py
git commit -m "deploy-detect: merge->PR->pending-applies->waves (script + action)"
```

---

### Task 6: `actions/apply-cell` — exact-plan apply

The core apply action, shared by `deploy.yml` (this PRD) and `apply.yml` (PRD 3). Downloads the reviewed `.otplan`, verifies the fingerprint, applies the **exact** stored plan, saves state, completes the apply check. Never re-plans.

**Files:**
- Create: `actions/apply-cell/action.yml`

**Interfaces:**
- Consumes: preview artifact `plan-<slug>-<env>` (from plan-cell) containing `<stack>/stack.otplan` + `fingerprint.txt`; `scripts/plan-classify --fingerprint-only` (Task 1); `actions/state` (PRD 0). `slug` = stack path with `/`→`-`.
- Produces: completes the `apply / <env> / <stack>` check-run on `head-sha` → `completed`/`success`.

- [ ] **Step 1: Write `actions/apply-cell/action.yml`**

```yaml
name: shipmate apply-cell
description: Apply one stack x env from its reviewed .otplan artifact; complete the apply check. Never re-plans.
inputs:
  stack:          { description: 'Stack path, e.g. stacks/app.', required: true }
  stack-name:     { description: 'Stack display name for the check, e.g. app.', required: true }
  env:            { description: 'Environment name, e.g. dev-eu.', required: true }
  head-sha:       { description: 'PR head SHA carrying the pending apply check.', required: true }
  preview-run-id: { description: 'Preview run id holding the reviewed plan artifact.', required: true }
  state-path:     { description: 'Flavor state path, e.g. stacks/app/.state.', required: true }
  github-token:   { description: 'GITHUB_TOKEN with checks:write and actions:read.', required: true }
runs:
  using: composite
  steps:
    - name: Stack slug (artifact names forbid '/')
      id: ids
      shell: bash
      env:
        STACK: ${{ inputs.stack }}
      run: echo "slug=$(printf '%s' "$STACK" | tr / -)" >> "$GITHUB_OUTPUT"

    - name: Download reviewed plan artifact (fail-safe if missing)
      shell: bash
      env:
        GH_TOKEN: ${{ inputs.github-token }}
        RUN_ID: ${{ inputs.preview-run-id }}
        ART: plan-${{ steps.ids.outputs.slug }}-${{ inputs.env }}
        ENV: ${{ inputs.env }}
        STACK_NAME: ${{ inputs.stack-name }}
      run: |
        set -euo pipefail
        if [ -z "$RUN_ID" ]; then
          echo "::error::no successful preview run for this head SHA — no reviewed plan to apply for $ENV/$STACK_NAME. Re-run preview."
          exit 1
        fi
        if ! gh run download "$RUN_ID" -n "$ART" -D . ; then
          echo "::error::reviewed plan artifact '$ART' missing/expired — cannot apply $ENV/$STACK_NAME without re-planning (fail-safe). Re-run preview."
          exit 1
        fi

    - name: Verify fingerprint matches the reviewed plan
      shell: bash
      env:
        ENV: ${{ inputs.env }}
        STACK_NAME: ${{ inputs.stack-name }}
      run: |
        set -euo pipefail
        mv fingerprint.txt expected-fingerprint.txt
        python3 "$GITHUB_ACTION_PATH/../../scripts/plan-classify" --fingerprint-only
        if ! diff -q fingerprint.txt expected-fingerprint.txt >/dev/null; then
          names=$(python3 -c "import os;print(' '.join(sorted(k for k in os.environ if k.startswith('TF_VAR_') or k=='TF_WORKSPACE')))")
          echo "::error::apply aborted for $ENV/$STACK_NAME: current env does not match the reviewed plan's fingerprint. Current variable names: $names. Values differ or a variable was added/removed — re-plan."
          exit 1
        fi

    - uses: shipmate-iac/shipmate/actions/state@__DEV_SHA__
      with:
        stack-slug: ${{ steps.ids.outputs.slug }}
        env: ${{ inputs.env }}
        mode: restore
        path: ${{ inputs.state-path }}

    - name: Apply the stored plan (exact-plan; stale -> fail-safe)
      shell: bash
      env:
        STACK: ${{ inputs.stack }}
      # The fixture 'apply' script runs `tofu apply stack.otplan` (honors the
      # auth subtree override). tofu rejects a plan whose state moved since it
      # was generated -> non-zero exit -> apply check stays pending (fail-safe).
      run: terramate script run --no-recursive -C "$STACK" apply

    - uses: shipmate-iac/shipmate/actions/state@__DEV_SHA__
      with:
        stack-slug: ${{ steps.ids.outputs.slug }}
        env: ${{ inputs.env }}
        mode: save
        path: ${{ inputs.state-path }}

    - name: Complete the apply check
      shell: bash
      env:
        GH_TOKEN: ${{ inputs.github-token }}
        ENV: ${{ inputs.env }}
        STACK_NAME: ${{ inputs.stack-name }}
        HEAD_SHA: ${{ inputs.head-sha }}
      run: |
        set -euo pipefail
        gh api "repos/$GITHUB_REPOSITORY/commits/$HEAD_SHA/check-runs?per_page=100" > checks.json
        # Author-controlled ENV/STACK_NAME pass via env; JSON handled in python.
        python3 - > patch.json <<'PY'
        import json, os
        name = f"apply / {os.environ['ENV']} / {os.environ['STACK_NAME']}"
        runs = json.load(open("checks.json"))["check_runs"]
        ids = [c["id"] for c in runs if c["name"] == name]
        if not ids:
            raise SystemExit(f"::warning::no apply check named '{name}' on head SHA; nothing to complete.")
        body = {"id": ids[0], "body": {"status": "completed", "conclusion": "success",
                "output": {"title": "applied", "summary": "Applied the reviewed plan for this stack x environment."}}}
        print(json.dumps(body))
        PY
        id=$(python3 -c "import json,sys;print(json.load(open('patch.json'))['id'])")
        python3 -c "import json;print(json.dumps(json.load(open('patch.json'))['body']))" > body.json
        gh api -X PATCH "repos/$GITHUB_REPOSITORY/check-runs/$id" --input body.json >/dev/null
```

Note: `__DEV_SHA__` is a placeholder — Task 8 pins it to this branch's HEAD SHA (the same way consumer workflows pin), and Task 10 finalizes to the merge SHA. Within one repo a composite action referencing a sibling action by SHA is normal (that is how consumers consume it); do not use a relative `./` local path — apply-cell is consumed cross-repo.

- [ ] **Step 2: Lint the manifest**

Run: `python -c "import yaml,sys; yaml.safe_load(open('actions/apply-cell/action.yml')); print('yaml ok')"`
Expected: `yaml ok`. Also eyeball: no `${{ }}` inside any `description:`; flow-style descriptions with `,`/`:` are single-quoted.

- [ ] **Step 3: Commit**

```bash
git add actions/apply-cell/action.yml
git commit -m "apply-cell: exact-plan apply from reviewed artifact + complete apply check"
```

---

### Task 7: `actions/drift-cell` — nightly drift plan + issue upsert

**Files:**
- Create: `actions/drift-cell/action.yml`

**Interfaces:**
- Consumes: `scripts/plan-classify` (Task 1); `actions/state` (restore only — drift never saves). Reuses the fixture `plan` script.
- Produces: one open labeled Issue per drifted stack×env (`drift: <env> / <stack-name>`, label `drift`); auto-closes it on a clean run.

- [ ] **Step 1: Write `actions/drift-cell/action.yml`**

```yaml
name: shipmate drift-cell
description: Plan one stack x env; open/update a drift Issue on changes, auto-close it when clean.
inputs:
  stack:         { description: 'Stack path, e.g. stacks/app.', required: true }
  stack-name:    { description: 'Stack display name, e.g. app.', required: true }
  env:           { description: 'Environment name, e.g. dev-eu.', required: true }
  state-path:    { description: 'Flavor state path, e.g. stacks/app/.state.', required: true }
  github-token:  { description: 'GITHUB_TOKEN with issues:write.', required: true }
  slack-webhook: { description: 'Optional Slack webhook URL; drift is posted when set.', required: false, default: "" }
runs:
  using: composite
  steps:
    - name: Stack slug
      id: ids
      shell: bash
      env: { STACK: ${{ inputs.stack }} }
      run: echo "slug=$(printf '%s' "$STACK" | tr / -)" >> "$GITHUB_OUTPUT"

    - uses: shipmate-iac/shipmate/actions/state@__DEV_SHA__
      with:
        stack-slug: ${{ steps.ids.outputs.slug }}
        env: ${{ inputs.env }}
        mode: restore
        path: ${{ inputs.state-path }}

    - name: Plan + classify drift
      id: plan
      shell: bash
      env: { STACK: ${{ inputs.stack }} }
      run: |
        set -euo pipefail
        terramate script run --no-recursive -C "$STACK" plan
        tofu -chdir="$STACK" show -json stack.otplan > plan.json
        python3 "$GITHUB_ACTION_PATH/../../scripts/plan-classify" plan.json

    - name: Upsert / close drift issue
      shell: bash
      env:
        GH_TOKEN: ${{ inputs.github-token }}
        ENV: ${{ inputs.env }}
        STACK_NAME: ${{ inputs.stack-name }}
        DRIFTED: ${{ steps.plan.outputs.changed }}
        ADD: ${{ steps.plan.outputs.add }}
        CHANGE: ${{ steps.plan.outputs.change }}
        DESTROY: ${{ steps.plan.outputs.destroy }}
      run: |
        set -euo pipefail
        gh label create drift --color FBCA04 --description "shipmate drift" --force >/dev/null 2>&1 || true
        title="drift: $ENV / $STACK_NAME"
        existing=$(gh issue list --label drift --state open --limit 200 --json number,title \
          | TITLE="$title" python3 -c "import json,os,sys;print(next((str(i['number']) for i in json.load(sys.stdin) if i['title']==os.environ['TITLE']),''))")
        if [ "$DRIFTED" = "true" ]; then
          body="Drift detected in \`$STACK_NAME\` @ \`$ENV\`: +$ADD ~$CHANGE -$DESTROY. Auto-closed on the next clean drift run."
          if [ -n "$existing" ]; then
            gh issue edit "$existing" --body "$body" >/dev/null
          else
            gh issue create --title "$title" --label drift --body "$body" >/dev/null
          fi
        elif [ -n "$existing" ]; then
          gh issue close "$existing" --comment "Drift resolved — clean plan for $ENV / $STACK_NAME." >/dev/null
        fi

    - name: Slack notify on drift (optional)
      if: ${{ inputs.slack-webhook != '' && steps.plan.outputs.changed == 'true' }}
      shell: bash
      env:
        SLACK: ${{ inputs.slack-webhook }}
        ENV: ${{ inputs.env }}
        STACK_NAME: ${{ inputs.stack-name }}
      run: |
        set -euo pipefail
        python3 - > payload.json <<'PY'
        import json, os
        print(json.dumps({"text": f":ocean: drift detected: {os.environ['ENV']} / {os.environ['STACK_NAME']}"}))
        PY
        curl -sS -X POST -H 'Content-Type: application/json' --data @payload.json "$SLACK" >/dev/null
```

- [ ] **Step 2: Lint the manifest**

Run: `python -c "import yaml; yaml.safe_load(open('actions/drift-cell/action.yml')); print('yaml ok')"`
Expected: `yaml ok`. Confirm no `${{ }}` inside `description:` fields; the two flow-style descriptions containing `;`/`,` are single-quoted.

- [ ] **Step 3: Commit**

```bash
git add actions/drift-cell/action.yml
git commit -m "drift-cell: plan + drift issue upsert/auto-close + optional Slack"
```

---

### Task 8: `deploy.yml` (stacks repo) + wave-apply acceptance

Engine code is done (Tasks 1-7). This task pins the sample workflow to the dev SHA, authors `deploy.yml` in `repo-example-stacks`, and drives acceptance criteria 1-5. Push the branch first so the sample workflow can pin real actions.

> **E2E task — observational, not TDD.** Steps drive live GitHub Actions and read run results. There is no unit runner for workflows (CLAUDE.md: sample repos ARE the harness).

**Files:**
- Create: `../repo-example-stacks/.github/workflows/deploy.yml`
- Modify (later, Task 10): folders + workspaces variants.

- [ ] **Step 1: Push the branch; record the dev SHA**

```bash
git push -u origin prd-2-deploy-waves-drift
git rev-parse HEAD   # -> DEV_SHA
```
Replace every `__DEV_SHA__` placeholder in `actions/apply-cell/action.yml` and `actions/drift-cell/action.yml` (the `shipmate-iac/shipmate/actions/state@__DEV_SHA__` refs) with `DEV_SHA`, commit, push, and re-record `DEV_SHA` (it changed). Repeat once so the pin matches the final tip. Sibling actions pinned by SHA — never `./` local paths (apply-cell is consumed cross-repo).

- [ ] **Step 2: Ensure `<env>-apply` environments carry the per-env vars**

Apply jobs bind `environment: <env>-apply`; `vars.TF_VAR_env`/`TF_VAR_region` resolve from that environment. Set them on each apply env of `repo-example-stacks` (env-level, not repo-level — they differ per env):
```bash
for e in dev-eu dev-us sbx; do
  gh variable set TF_VAR_env    --env "${e}-apply" --body "$e" --repo shipmate-iac/repo-example-stacks
  gh variable set TF_VAR_region --env "${e}-apply" --body "eu-west-1" --repo shipmate-iac/repo-example-stacks
done
```
(Use each env's intended region; mirror whatever the plan-side `dev-eu`/`dev-us`/`sbx` vars are set to.)

- [ ] **Step 2b: Fix the stacks `preview.yml` state path (PRD-1 carryover)**

The stacks flavor's real backend is `<stack>/.state/...` (tofu runs `-chdir=stack`), but PRD-1's `preview.yml` restores/saves `actions/state` at repo-root `.state`. Plan-only never exercised it; deploy's apply-cell restores the **same** path plan-cell used, so plan-time and apply-time state must agree. In `../repo-example-stacks/.github/workflows/preview.yml`, change the `shipmate/actions/state` step's `path: .state` → `path: ${{ matrix.stack }}/.state`. Commit + push; re-run preview on an open PR so the reviewed artifacts are regenerated with aligned state. (folders/workspaces already use correct per-flavor paths — no change.)

- [ ] **Step 3: Author `../repo-example-stacks/.github/workflows/deploy.yml`**

```yaml
name: deploy
on:
  push:
    branches: [main]
concurrency:
  group: deploy-main
  cancel-in-progress: false
permissions:
  contents: read
  checks: write
  pull-requests: read
  actions: read
jobs:
  detect:
    runs-on: ubuntu-latest
    outputs:
      wave0: ${{ steps.d.outputs.wave0 }}
      wave1: ${{ steps.d.outputs.wave1 }}
      wave2: ${{ steps.d.outputs.wave2 }}
      wave3: ${{ steps.d.outputs.wave3 }}
      wave4: ${{ steps.d.outputs.wave4 }}
      wave5: ${{ steps.d.outputs.wave5 }}
      wave6: ${{ steps.d.outputs.wave6 }}
      wave7: ${{ steps.d.outputs.wave7 }}
      head-sha: ${{ steps.d.outputs.head-sha }}
      preview-run-id: ${{ steps.d.outputs.preview-run-id }}
      empty: ${{ steps.d.outputs.empty }}
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2
        with: { fetch-depth: 0 }
      - uses: shipmate-iac/shipmate/actions/setup@DEV_SHA
        with:
          terramate-version: ${{ vars.TERRAMATE_VERSION }}
          tofu-version: ${{ vars.TOFU_VERSION }}
      - id: d
        uses: shipmate-iac/shipmate/actions/deploy-detect@DEV_SHA
        with:
          base-sha: ${{ github.event.before }}
          github-token: ${{ github.token }}

  wave0:
    needs: detect
    if: ${{ !failure() && !cancelled() && needs.detect.outputs.wave0 != '[]' }}
    runs-on: ubuntu-latest
    permissions: { contents: read, checks: write, actions: read }
    strategy:
      fail-fast: false
      matrix: ${{ fromJSON(needs.detect.outputs.wave0) }}
    environment: ${{ matrix.environment }}-apply
    concurrency:
      group: apply-${{ matrix.environment }}-${{ matrix.stack }}
      cancel-in-progress: false
    name: apply / ${{ matrix.environment }} / ${{ matrix.stack }}
    env:
      TF_VAR_env: ${{ vars.TF_VAR_env }}
      TF_VAR_region: ${{ vars.TF_VAR_region }}
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2
        with: { fetch-depth: 0 }
      - uses: shipmate-iac/shipmate/actions/setup@DEV_SHA
        with:
          terramate-version: ${{ vars.TERRAMATE_VERSION }}
          tofu-version: ${{ vars.TOFU_VERSION }}
      - uses: shipmate-iac/shipmate/actions/apply-cell@DEV_SHA
        with:
          stack: ${{ matrix.stack }}
          stack-name: ${{ matrix.stack }}
          env: ${{ matrix.environment }}
          head-sha: ${{ needs.detect.outputs.head-sha }}
          preview-run-id: ${{ needs.detect.outputs.preview-run-id }}
          state-path: ${{ matrix.stack }}/.state
          github-token: ${{ github.token }}

  # wave1..wave7: IDENTICAL to wave0 except:
  #   - job id `waveN`; matrix reads `needs.detect.outputs.waveN`; name unchanged
  #   - `needs:` lists detect + every earlier wave, so ordering + skip/fail
  #     propagation hold. Guard reads its own waveN:
  #       if: ${{ !failure() && !cancelled() && needs.detect.outputs.waveN != '[]' }}
  #   needs per wave:
  #     wave1: [detect, wave0]
  #     wave2: [detect, wave0, wave1]
  #     wave3: [detect, wave0, wave1, wave2]
  #     wave4: [detect, wave0, wave1, wave2, wave3]
  #     wave5: [detect, wave0, wave1, wave2, wave3, wave4]
  #     wave6: [detect, wave0, wave1, wave2, wave3, wave4, wave5]
  #     wave7: [detect, wave0, wave1, wave2, wave3, wave4, wave5, wave6]
  # (Write all 8 jobs out in full — do NOT use a comment in the real file.)

  summary:
    needs: [detect, wave0, wave1, wave2, wave3, wave4, wave5, wave6, wave7]
    if: always()
    runs-on: ubuntu-latest
    permissions: { checks: write }
    steps:
      - name: Complete checkmate on the merged PR head SHA
        env:
          GH_TOKEN: ${{ github.token }}
          HEAD_SHA: ${{ needs.detect.outputs.head-sha }}
          RESULTS: ${{ join(needs.*.result, ',') }}
        run: |
          set -euo pipefail
          if echo "$RESULTS" | grep -q failure; then concl=failure; title="deploy incomplete";
          else concl=success; title="all waves applied"; fi
          python3 - "$HEAD_SHA" "$concl" "$title" > body.json <<'PY'
          import json, sys
          head, concl, title = sys.argv[1:4]
          print(json.dumps({"name": "shipmate / checkmate", "head_sha": head,
              "status": "completed", "conclusion": concl,
              "output": {"title": title, "summary": "Deploy wave applies completed."}}))
          PY
          gh api "repos/$GITHUB_REPOSITORY/check-runs" --input body.json >/dev/null
      - name: Slack on failed deploy (outranks drift)
        if: ${{ contains(join(needs.*.result, ','), 'failure') && vars.SLACK_WEBHOOK != '' }}
        env:
          SLACK: ${{ vars.SLACK_WEBHOOK }}
        run: |
          set -euo pipefail
          python3 -c "import json;open('p.json','w').write(json.dumps({'text':':rotating_light: shipmate deploy failed on main — a wave apply failed.'}))"
          curl -sS -X POST -H 'Content-Type: application/json' --data @p.json "$SLACK" >/dev/null
```

Write out wave1..wave7 in full per the comment block (the comment must NOT remain in the committed file). Commit + push the sample repo.

- [ ] **Step 4: Acceptance 1 — wave-distant PR applies in graph order (cross-env edge)**

Open a PR on `repo-example-stacks` touching `stacks/dns` (in dev-us) and `stacks/tenant-a`. Let preview run (plans + pending apply checks). Merge. Watch `deploy`:
- `detect` emits dns in an early wave, tenant-a in a later wave.
- Verify from job **start timestamps**: `dns@dev-us` wave completes before `platform@dev-eu`'s wave starts (cross-env edge respected), and tenant-a applies last.
- All touched cells' `apply / <env> / <stack>` checks flip to success on the PR head SHA.

Command to inspect: `gh run view <run-id> --repo shipmate-iac/repo-example-stacks --json jobs --jq '.jobs[] | {name, startedAt, conclusion}'`.

- [ ] **Step 5: Acceptance 2 — empty middle waves + skip-propagation**

PR touching `stacks/dns` and `stacks/app` (nothing between). Merge. Verify: dns applies (wave 0), the intermediate waves (platform/auth/workers) are **skipped** (empty), app still applies (its wave), both apply checks succeed. Confirms the skip-propagation guard (empty wave does not skip its successors).

- [ ] **Step 6: Acceptance 4 — kill a wave-1 cell mid-run, then recover**

PR touching `stacks/dns` + `stacks/platform`. Enable the precondition fixture on `platform` (`TF_VAR_fail_precondition=true` via the plan, OR the PRD-0 failure fixture) so its apply fails. Merge. Verify:
- `dns` (wave 0) applies; `platform` (wave 1) **fails**; later waves **skip**; `platform`'s apply check stays pending/failed; Slack (if configured) fires.
- Remove the fixture / re-run the failed `platform` job → it applies from its still-fresh reviewed plan, check completes success.

- [ ] **Step 7: Acceptance 5 — pre-merge apply → deploy no-op + checkmate**

Simulate PRD 3: before merging a PR that touches `stacks/dns`, manually complete its apply check (mark the `apply / dev-eu / stacks/dns` check success on the PR head SHA via `gh api`, mimicking `mate apply`). Merge. Verify `detect` **skips** dns (already-completed check → no-op), `deploy` shows no dns apply job, and `summary` completes `shipmate / checkmate` on the head SHA.

- [ ] **Step 8: Acceptance 3 — two PRs in quick succession (revised, Deviation B)**

Merge two PRs (each touching a different stack) within seconds. Verify:
- If neither deploy run is superseded → both PRs' stacks apply, both sets of checks succeed.
- If GHA drops the intermediate queued run → the superseded PR's apply checks stay **pending + visible** on that merged PR (nothing silently lost). Re-run that PR's `deploy` workflow → its stacks apply and checks complete.
- Record which occurred in the run notes.

- [ ] **Step 9: Commit the sample workflow + record progress**

```bash
cd ../repo-example-stacks && git add .github/workflows/deploy.yml && git commit -m "deploy.yml: wave applies (shipmate PRD 2)" && git push
```
Note the green run ids + acceptance observations in the ledger scratch (Task 10 folds them in).

---

### Task 9: `drift.yml` (stacks repo) + drift acceptance

**Files:**
- Create: `../repo-example-stacks/.github/workflows/drift.yml`

- [ ] **Step 1: Author `../repo-example-stacks/.github/workflows/drift.yml`**

```yaml
name: drift
on:
  schedule:
    - cron: "17 3 * * *"   # nightly, off-peak
  workflow_dispatch: {}     # manual trigger for acceptance
permissions:
  contents: read
  issues: write
jobs:
  detect:
    runs-on: ubuntu-latest
    outputs:
      matrix: ${{ steps.m.outputs.matrix }}
      empty: ${{ steps.m.outputs.empty }}
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2
        with: { fetch-depth: 0 }
      - uses: shipmate-iac/shipmate/actions/setup@DEV_SHA
        with:
          terramate-version: ${{ vars.TERRAMATE_VERSION }}
          tofu-version: ${{ vars.TOFU_VERSION }}
      - id: m
        uses: shipmate-iac/shipmate/actions/build-matrix@DEV_SHA
        with:
          base-sha: ""
          all-stacks: "true"
  drift:
    needs: detect
    if: needs.detect.outputs.empty == 'false'
    runs-on: ubuntu-latest
    permissions: { contents: read, issues: write }
    strategy:
      fail-fast: false
      matrix: ${{ fromJSON(needs.detect.outputs.matrix) }}
    environment: ${{ matrix.environment }}
    name: drift / ${{ matrix.environment }} / ${{ matrix.stack }}
    env:
      TF_VAR_env: ${{ vars.TF_VAR_env }}
      TF_VAR_region: ${{ vars.TF_VAR_region }}
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2
        with: { fetch-depth: 0 }
      - uses: shipmate-iac/shipmate/actions/setup@DEV_SHA
        with:
          terramate-version: ${{ vars.TERRAMATE_VERSION }}
          tofu-version: ${{ vars.TOFU_VERSION }}
      - uses: shipmate-iac/shipmate/actions/drift-cell@DEV_SHA
        with:
          stack: ${{ matrix.stack }}
          stack-name: ${{ matrix.stack }}
          env: ${{ matrix.environment }}
          state-path: ${{ matrix.stack }}/.state
          github-token: ${{ github.token }}
          slack-webhook: ${{ vars.SLACK_WEBHOOK }}
```

(`environment: ${{ matrix.environment }}` uses the **plan** env — drift only plans, never applies.)

- [ ] **Step 2: Acceptance 6 — manufactured drift → issue → auto-close**

1. Seed state for a stack×env (run a deploy/apply so `.state` exists in cache), then apply the PRD-0 drift fixture (`tools/mutate-state.ps1` or equivalent — mutate the persisted state so the next plan shows a diff).
2. `workflow_dispatch` the `drift` workflow. Verify a labeled Issue `drift: <env> / <stack>` is created (label `drift`).
3. Re-run `drift` **without** re-mutating (clean plan). Verify the same Issue **auto-closes** with the "Drift resolved" comment.
4. Confirm re-running with drift still present **updates** (does not duplicate) the issue.

- [ ] **Step 3: Commit**

```bash
cd ../repo-example-stacks && git add .github/workflows/drift.yml && git commit -m "drift.yml: nightly drift detection (shipmate PRD 2)" && git push
```

---

### Task 10: Public flip, protection, generalization, ledger, docs

Finalizes the PRD: makes protection testable, proves generalization (criterion 7), updates the ledger + docs, and pins to the merge SHA.

- [ ] **Step 1: Secret-scan then flip all 3 sample repos public (Q2)**

Scan each repo's full history before exposing it:
```bash
for r in repo-example-stacks repo-example-folders repo-example-workspaces; do
  echo "== $r ==" ; ( cd ../$r && git log -p --all | grep -nEi 'aws_secret|password|BEGIN [A-Z ]*PRIVATE KEY|ghp_|token\s*=' || echo "clean" )
done
```
Expected: `clean` for all (null resources, zero creds). **If anything matches, STOP and surface to the human — do not flip.** On clean:
```bash
for r in repo-example-stacks repo-example-folders repo-example-workspaces; do
  gh repo edit shipmate-iac/$r --visibility public --accept-visibility-change-consequences
done
```
This is outward-facing + hard to reverse — confirm with the human immediately before running.

- [ ] **Step 2: Enable `<env>-apply` reviewer protection + prove the protected-cell pause**

On `repo-example-stacks`, add a required reviewer to one apply env (e.g. `sbx-apply`) via the API (now allowed on public repos). Open + merge a PR touching a stack tagged into that env. Verify the wave **pauses at that cell** awaiting approval (visible), approve → it proceeds. Document that protection pauses only that cell, not the wave definition (acceptance from the PRD Risks section).

- [ ] **Step 3: Enable PRD-1's deferred required-check gate**

Now that repos are public, create the branch-protection / ruleset requiring `shipmate / checkmate` on `main` for `repo-example-stacks` (the gate PRD-1 documented but could not enforce on free-tier). Confirm a PR with an incomplete preview is blocked from merge. Update `docs/branch-protection.md` to note it is now enforced on the public sample repos.

- [ ] **Step 4: Acceptance 7 — generalize deploy + drift to folders + workspaces**

Copy `deploy.yml` + `drift.yml` into `repo-example-folders` and `repo-example-workspaces`, adjusting **only** the per-flavor `env:` block and `state-path` (no shipmate code change):
- **folders:** no `TF_VAR_*`/`TF_WORKSPACE` env; `state-path: <leaf>/terraform.tfstate` (the leaf is the stack path).
- **workspaces:** `env: { TF_WORKSPACE: ${{ vars.TF_WORKSPACE }} }` (no `TF_VAR_*`); `state-path: terraform.tfstate.d`. Set `TF_WORKSPACE` on each `<env>-apply` environment too.
Set `<env>-apply` vars on both repos as in Task 8 Step 2 (folders: none needed). Run a wave-apply PR on each; verify both go green (folders = 1:1 single-env waves; workspaces = `TF_WORKSPACE` selected per env) with the **same pinned DEV_SHA**. Commit + push both.

- [ ] **Step 5: Append the PRD-2 line-count ledger**

Count actual lines of the new/changed shipmate files and append rows to `LEDGER.md` (do not rewrite PRD 0/1 rows):
```bash
wc -l scripts/plan-classify scripts/waves scripts/deploy-detect \
      actions/apply-cell/action.yml actions/deploy-detect/action.yml actions/drift-cell/action.yml
git diff 2478c05 -- scripts/build-matrix actions/build-matrix/action.yml actions/plan-cell/action.yml | grep -c '^+'
```
Add a PRD-2 total row and the new running total. **If running total > ~600, add a bold decision-gate flag line** and surface it to the human (brief criterion). Commit.

- [ ] **Step 6: README + docs**

Add a PRD-2 section to `README.md` (deploy waves + drift, the plan→store→review→apply model, the Terrateam-parity note). Note Deviations A/B in `docs/` (or link the design spec). Commit.

- [ ] **Step 7: Finalize SHA pins**

After the branch merges (or at merge prep): bump every `@DEV_SHA` in the 3 sample repos' `deploy.yml`/`drift.yml` and the `@__DEV_SHA__`→`@DEV_SHA` refs in `apply-cell`/`drift-cell` to the final merge SHA. (Handled at branch-finishing time per superpowers:finishing-a-development-branch.)

- [ ] **Step 8: Commit the ledger + docs**

```bash
git add LEDGER.md README.md docs/
git commit -m "PRD 2: ledger, README, docs (deploy waves + drift)"
```

---

## Self-Review (author checklist — completed before handoff)

**Spec coverage:** apply-cell (§1)→T6; waves (§2)→T3; deploy-detect (§2b)→T5; deploy.yml (§3)→T8; drift.yml (§4)→T9; drift-cell→T7; public flip (§5/Q2)→T10.1; fingerprint+TF_WORKSPACE (§5/Q4)→T1+T2; build-matrix all-stacks→T4; Deviation A→T5 design; Deviation B→T8.8 acceptance; all 7 acceptance criteria→T8/T9/T10. Budget→T10.5 with decision-gate flag. No gaps.

**Placeholder scan:** `__DEV_SHA__`/`DEV_SHA` are intentional, resolved in T8.1/T10.7. The wave1-7 comment block in T8.3 explicitly instructs full expansion. No TBD/TODO/"handle errors"/"similar to".

**Type consistency:** `classify`/`fingerprint` (T1) used by T2/T6/T7; `compute_cells(all_stacks, base)` (T4) used by T5; `parse_dot`/`levels`/`assign_waves`/`MAX_WAVES` (T3) used by T5; `filter_pending` (T5). Cell shape `{stack, environment, workload}` consistent across build-matrix/waves/deploy-detect. Check name `apply / <env> / <stack>` verbatim in plan-cell (create) / deploy-detect (filter) / apply-cell (complete). Consistent.

