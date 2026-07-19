# scripts/tests/test_summary_comment.py
import importlib.util
import pathlib
from importlib.machinery import SourceFileLoader

_p = pathlib.Path(__file__).resolve().parents[1] / "summary-comment"
_loader = SourceFileLoader("summary_comment", str(_p))
_spec = importlib.util.spec_from_loader("summary_comment", _loader)
sc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sc)


def _cell(**kw):
    base = {
        "stack": "stacks/app",
        "stack_path": "stacks/app",
        "environment": "dev-eu",
        "add": 1,
        "change": 0,
        "destroy": 0,
        "changed": True,
    }
    base.update(kw)
    return base


# --- diff_map -------------------------------------------------------------


def test_diff_map_moves_signs_to_column_zero_preserving_indent():
    text = (
        '  + resource "null_resource" "a" {\n'
        "      + id = (known after apply)\n"
        "  - resource removed\n"
        "  ~ resource updated in-place"
    )
    assert sc.diff_map(text) == (
        '+   resource "null_resource" "a" {\n'
        "+       id = (known after apply)\n"
        "-   resource removed\n"
        "!   resource updated in-place"
    )


def test_diff_map_handles_replace_sign_and_leaves_plain_lines():
    text = "  -/+ resource replaced\n  # null_resource.a will be created\nplain"
    out = sc.diff_map(text).splitlines()
    assert out[0].startswith("-/+")
    assert out[1] == "  # null_resource.a will be created"
    assert out[2] == "plain"


def test_diff_map_does_not_touch_interior_tildes():
    assert sc.diff_map("value = ~/.config") == "value = ~/.config"


# --- fence ----------------------------------------------------------------


def test_fence_grows_past_backtick_runs_in_plan_text():
    text = "x = ```code```"
    fenced = sc.fence(text)
    assert fenced.startswith("````diff\n")
    assert fenced.endswith("\n````")


def test_fence_minimum_three_backticks():
    assert sc.fence("no ticks").startswith("```diff\n")


# --- emoji / escape ---------------------------------------------------------


def test_emoji_verdicts():
    assert sc.emoji(_cell(changed=False, add=0)) == "🟢"
    assert sc.emoji(_cell(destroy=2)) == "🔴"
    assert sc.emoji(_cell()) == "🟡"


def test_md_escape_neutralizes_pipes_and_newlines():
    assert sc._md_escape("a|b\nc") == "a\\|b c"
