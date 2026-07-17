import importlib.util
import pathlib
from importlib.machinery import SourceFileLoader

_D = pathlib.Path(__file__).resolve().parents[1]
_FIX = _D / "tests" / "fixtures" / "run-graph-stacks.dot"


def _load(fname):
    loader = SourceFileLoader(fname.replace("-", "_"), str(_D / fname))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


ad = _load("apply-detect")


def test_workset_matches_plan_artifacts_for_env():
    names = [
        "plan-stacks-app-dev-eu",
        "plan-stacks-dns-dev-eu",
        "plan-stacks-app-dev-us",  # other env — excluded
        "cell-summary-stacks-app-dev-eu",
    ]  # not a plan artifact — excluded
    graph_paths = ["stacks/app", "stacks/dns", "stacks/platform"]
    cells = ad.workset_from_artifacts(names, "dev-eu", graph_paths)
    stacks = sorted(c["stack"] for c in cells)
    assert stacks == ["stacks/app", "stacks/dns"]
    assert all(c["environment"] == "dev-eu" for c in cells)


def test_workset_ignores_slug_with_wrong_env_suffix():
    names = ["plan-stacks-app-dev-eu-apply"]  # not the plain env
    cells = ad.workset_from_artifacts(names, "dev-eu", ["stacks/app"])
    assert cells == []


def test_filter_pending_drops_completed():
    cells = [
        {"stack": "stacks/app", "environment": "dev-eu"},
        {"stack": "stacks/dns", "environment": "dev-eu"},
    ]
    completed = {"apply / dev-eu / stacks/dns"}
    kept = ad.filter_pending(cells, completed)
    assert [c["stack"] for c in kept] == ["stacks/app"]


def test_cross_env_block_when_upstream_pending_in_other_env():
    # app depends on platform; platform not in this workset but pending under dev-us
    cells = [{"stack": "stacks/app", "environment": "dev-eu"}]
    deps = {"stacks/app": {"stacks/platform"}, "stacks/platform": set()}
    pending_other = {"stacks/platform": "apply / dev-us / stacks/platform"}
    block = ad.cross_env_block(cells, deps, pending_other)
    assert block == "apply / dev-us / stacks/platform"


def test_cross_env_no_block_when_upstream_in_workset():
    cells = [
        {"stack": "stacks/app", "environment": "dev-eu"},
        {"stack": "stacks/platform", "environment": "dev-eu"},
    ]
    deps = {"stacks/app": {"stacks/platform"}, "stacks/platform": set()}
    pending_other = {"stacks/platform": "apply / dev-us / stacks/platform"}
    assert ad.cross_env_block(cells, deps, pending_other) is None


def test_cross_env_no_block_when_no_other_env_pending():
    cells = [{"stack": "stacks/app", "environment": "dev-eu"}]
    deps = {"stacks/app": {"stacks/platform"}, "stacks/platform": set()}
    assert ad.cross_env_block(cells, deps, {}) is None


def test_cross_env_no_block_when_upstream_applied_this_env_on_retry():
    # platform (shared stack) was already applied in dev-eu on a prior partial
    # run, so it's no longer in `pending` — but it IS still in this env's full
    # work set, so it must not be mistaken for an out-of-env stack even though
    # it also happens to be pending under dev-us.
    app_cell = {"stack": "stacks/app", "environment": "dev-eu"}
    deps = {"stacks/app": {"stacks/platform"}, "stacks/platform": set()}
    pending_other = {"stacks/platform": "apply / dev-us / stacks/platform"}
    block = ad.cross_env_block(
        [app_cell],
        deps,
        pending_other,
        env_stacks={"stacks/app", "stacks/platform"},
    )
    assert block is None
