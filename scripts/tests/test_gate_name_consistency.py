"""Freeze the aggregate gate check name across its writers.

The gate `shipmate / gate` is created pending by `actions/summary`, completed
pre-merge by `actions/gate-refresh`, and completed post-merge inline in
`deploy.yml`. All three must emit the byte-identical name, or the gate greens
on one path and sticks on another. This guards that invariant the same way
test_check_runs_filter_aligned guards the check-runs read discipline.
"""

import pathlib

ENGINE = pathlib.Path(__file__).resolve().parents[2]
# Generated / third-party / VCS dirs: never shipmate source, and their contents
# (compiled .pyc constant pools, vendored packages) can carry the retired token
# for reasons unrelated to this repo -- scanning them would false-fail the guard.
SKIP_DIRS = {
    ".git",
    ".superpowers",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "node_modules",
}
GATE = "shipmate / gate"
WRITERS = [
    "actions/summary/action.yml",
    "actions/gate-refresh/action.yml",
    ".github/workflows/deploy.yml",
]


def test_gate_literal_present_in_every_writer():
    for rel in WRITERS:
        text = (ENGINE / rel).read_text(encoding="utf-8")
        assert GATE in text, f"{rel} is missing the gate literal {GATE!r}"


# Assembled so THIS file never contains the retired token as a literal
# substring -- writing it out would self-match and the test could never pass.
RETIRED = "check" + "mate"


def test_no_retired_gate_token_survivors():
    hits = []
    for p in ENGINE.rglob("*"):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError):
            continue
        if RETIRED in text:
            hits.append(str(p.relative_to(ENGINE)))
    assert not hits, f"stale {RETIRED!r} token in: {hits}"
