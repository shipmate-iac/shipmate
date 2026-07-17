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


ra = _load("register-app")


def test_main_stores_id_as_variable_and_pem_as_secret(monkeypatch):
    # Guards the id/pem parse+store: a swap (id<->pem) or wrong gh subcommand
    # would only surface during a real one-time registration otherwise.
    monkeypatch.setenv("MANIFEST_CODE", "code123")
    monkeypatch.setenv("GITHUB_REPOSITORY", "org/repo")
    calls = []

    def fake_run(args, **kw):
        calls.append(args)
        if "conversions" in args[-1]:
            return '{"id": 42, "pem": "PRIVATE_KEY", "slug": "shipmate"}'
        return ""

    monkeypatch.setattr(ra, "_run", fake_run)
    ra.main()

    assert calls[0] == ["gh", "api", "-X", "POST", "app-manifests/code123/conversions"]
    assert [
        "gh",
        "variable",
        "set",
        "SHIPMATE_APP_ID",
        "--repo",
        "org/repo",
        "--body",
        "42",
    ] in calls
    assert [
        "gh",
        "secret",
        "set",
        "SHIPMATE_APP_PRIVATE_KEY",
        "--repo",
        "org/repo",
        "--body",
        "PRIVATE_KEY",
    ] in calls
