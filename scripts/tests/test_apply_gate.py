import importlib.util
import pathlib
from importlib.machinery import SourceFileLoader

_D = pathlib.Path(__file__).resolve().parents[1]


def _load(fname):
    loader = SourceFileLoader(fname.replace("-", "_"), str(_D / fname))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


ag = _load("apply-gate")


def _run(name, status, conclusion, started_at="2026-07-18T10:00:00Z", run_id=1):
    return {
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "started_at": started_at,
        "id": run_id,
    }


def test_all_applies_succeeded():
    runs = [
        _run("apply / dev-eu / stacks/app", "completed", "success"),
        _run("apply / dev-us / stacks/app", "completed", "success"),
    ]
    assert ag.verdict(runs) == "complete"


def test_one_apply_still_queued():
    runs = [
        _run("apply / dev-eu / stacks/app", "completed", "success"),
        _run("apply / dev-us / stacks/app", "queued", None),
    ]
    assert ag.verdict(runs) == "pending"


def test_failed_apply_does_not_complete_gate():
    runs = [
        _run("apply / dev-eu / stacks/app", "completed", "failure"),
        _run("apply / dev-us / stacks/app", "completed", "success"),
    ]
    assert ag.verdict(runs) == "pending"


def test_neutral_no_change_apply_counts_as_done():
    # plan-cell completes a no-changes cell's apply check with conclusion=neutral.
    runs = [
        _run("apply / dev-eu / stacks/app", "completed", "neutral"),
        _run("apply / dev-us / stacks/app", "completed", "success"),
    ]
    assert ag.verdict(runs) == "complete"


def test_latest_run_per_name_wins():
    # A re-created check (e.g. preview rerun superseding an older pending one)
    # must be judged by its newest run, picked by (started_at, id).
    runs = [
        _run(
            "apply / dev-eu / stacks/app",
            "queued",
            None,
            started_at="2026-07-18T10:00:00Z",
            run_id=1,
        ),
        _run(
            "apply / dev-eu / stacks/app",
            "completed",
            "success",
            started_at="2026-07-18T11:00:00Z",
            run_id=2,
        ),
    ]
    assert ag.verdict(runs) == "complete"


def test_stale_completed_run_does_not_mask_newer_pending():
    runs = [
        _run(
            "apply / dev-eu / stacks/app",
            "completed",
            "success",
            started_at="2026-07-18T10:00:00Z",
            run_id=1,
        ),
        _run(
            "apply / dev-eu / stacks/app",
            "queued",
            None,
            started_at="2026-07-18T11:00:00Z",
            run_id=2,
        ),
    ]
    assert ag.verdict(runs) == "pending"


def test_non_apply_checks_ignored():
    runs = [
        _run("plan / dev-eu / stacks/app", "completed", "success"),
        _run("shipmate / checkmate", "queued", None),
        _run("apply / dev-eu / stacks/app", "completed", "success"),
    ]
    assert ag.verdict(runs) == "complete"


def test_no_apply_checks_at_all():
    runs = [_run("plan / dev-eu / stacks/app", "completed", "success")]
    assert ag.verdict(runs) == "no-applies"


def test_cancelled_apply_stays_pending():
    runs = [_run("apply / dev-eu / stacks/app", "completed", "cancelled")]
    assert ag.verdict(runs) == "pending"


def test_done_names_excludes_completed_failure():
    runs = [_run("apply / dev-eu / stacks/app", "completed", "failure")]
    assert ag.done_names(runs) == set()


def test_done_names_includes_completed_success_and_neutral():
    runs = [
        _run("apply / dev-eu / stacks/app", "completed", "success"),
        _run("apply / dev-us / stacks/app", "completed", "neutral"),
    ]
    assert ag.done_names(runs) == {"apply / dev-eu / stacks/app", "apply / dev-us / stacks/app"}


def test_done_names_uses_latest_run_per_name():
    runs = [
        _run(
            "apply / dev-eu / stacks/app",
            "completed",
            "success",
            started_at="2026-07-18T10:00:00Z",
            run_id=1,
        ),
        _run(
            "apply / dev-eu / stacks/app",
            "queued",
            None,
            started_at="2026-07-18T11:00:00Z",
            run_id=2,
        ),
    ]
    assert ag.done_names(runs) == set()


def test_latest_by_name_ignores_non_apply_checks():
    runs = [_run("plan / dev-eu / stacks/app", "completed", "success")]
    assert ag.latest_by_name(runs) == {}
