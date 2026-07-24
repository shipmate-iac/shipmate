import importlib.util
import pathlib
from importlib.machinery import SourceFileLoader

_p = pathlib.Path(__file__).resolve().parents[1] / "doctor"
_loader = SourceFileLoader("doctor", str(_p))
_spec = importlib.util.spec_from_loader("doctor", _loader)
doctor = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(doctor)

_REPO = "o/r"
_APP_ID = "999"
_BRANCH = "main"
_ENVS = {"dev-eu"}


def _gate_rule(integration_id=999, strict=True):
    return [
        {
            "type": "required_status_checks",
            "parameters": {
                "strict_required_status_checks_policy": strict,
                "required_status_checks": [
                    {"context": "shipmate / gate", "integration_id": integration_id},
                ],
            },
        },
    ]


def _environments(*names):
    return {"environments": [{"name": n} for n in names]}


def test_healthy_repo_emits_nothing(monkeypatch):
    responses = {
        f"repos/{_REPO}/rules/branches/{_BRANCH}": _gate_rule(),
        f"repos/{_REPO}/environments": _environments("dev-eu", "dev-eu-apply"),
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    assert doctor.warnings(_REPO, _APP_ID, _ENVS, _BRANCH) == []


def test_missing_environment_pair_warned(monkeypatch):
    responses = {
        f"repos/{_REPO}/rules/branches/{_BRANCH}": _gate_rule(),
        # dev-eu-apply is missing
        f"repos/{_REPO}/environments": _environments("dev-eu"),
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor.warnings(_REPO, _APP_ID, _ENVS, _BRANCH)
    assert len(out) == 1
    assert "dev-eu-apply" in out[0]


def test_gate_rule_wrong_integration_id_warned(monkeypatch):
    responses = {
        f"repos/{_REPO}/rules/branches/{_BRANCH}": _gate_rule(integration_id=15368),
        f"repos/{_REPO}/environments": _environments("dev-eu", "dev-eu-apply"),
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor.warnings(_REPO, _APP_ID, _ENVS, _BRANCH)
    assert len(out) == 1
    assert "integration_id" in out[0]
    assert "15368" in out[0]


def test_gate_rule_absent_warned(monkeypatch):
    responses = {
        # no required_status_checks rule at all
        f"repos/{_REPO}/rules/branches/{_BRANCH}": [{"type": "deletion", "parameters": {}}],
        f"repos/{_REPO}/environments": _environments("dev-eu", "dev-eu-apply"),
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor.warnings(_REPO, _APP_ID, _ENVS, _BRANCH)
    assert len(out) == 1
    assert "ungated" in out[0] or "not gated" in out[0]


def test_strict_policy_off_warned(monkeypatch):
    responses = {
        f"repos/{_REPO}/rules/branches/{_BRANCH}": _gate_rule(strict=False),
        f"repos/{_REPO}/environments": _environments("dev-eu", "dev-eu-apply"),
    }
    monkeypatch.setattr(doctor, "_gh_json", lambda path: responses[path])
    out = doctor.warnings(_REPO, _APP_ID, _ENVS, _BRANCH)
    assert len(out) == 1
    assert "up to date" in out[0]


def test_probe_403_degrades_to_note_not_failure(monkeypatch):
    # rules/branches probe raises the REAL failure type: bm.gh_json (via
    # build-matrix's _run) hard-fails a nonzero `gh api` exit with
    # `raise SystemExit(...)`, not a plain Exception -- SystemExit derives
    # from BaseException, so a catch of only `except Exception` would let
    # this propagate right past warnings(). This test simulates that exact
    # 403-on-rules/branches case. The environments probe still succeeds and
    # its own finding must still surface alongside the degrade note -- one
    # probe failing must not swallow the other, and no exception may escape
    # warnings() (no pytest.raises here -- a regression fails this test with
    # an uncaught SystemExit error, not a silent pass).
    def fake_gh_json(path):
        if "rules/branches" in path:
            raise SystemExit("::error::command failed (1): gh api ...")
        return _environments("dev-eu")  # dev-eu-apply missing -> its own warning

    monkeypatch.setattr(doctor, "_gh_json", fake_gh_json)
    out = doctor.warnings(_REPO, _APP_ID, _ENVS, _BRANCH)
    assert len(out) == 2
    assert any("could not verify" in line and "probe skipped" in line for line in out)
    assert any("dev-eu-apply" in line for line in out)


def test_probe_generic_exception_degrades_to_note(monkeypatch):
    # Non-SystemExit failures (e.g. a network error inside _run before it
    # even gets to check the return code) must degrade the same way.
    def fake_gh_json(path):
        if "rules/branches" in path:
            raise RuntimeError("connection reset")
        return _environments("dev-eu", "dev-eu-apply")

    monkeypatch.setattr(doctor, "_gh_json", fake_gh_json)
    out = doctor.warnings(_REPO, _APP_ID, _ENVS, _BRANCH)
    assert len(out) == 1
    assert "could not verify" in out[0] and "probe skipped" in out[0]
