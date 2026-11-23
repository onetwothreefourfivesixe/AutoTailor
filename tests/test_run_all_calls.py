"""`autotailor run` calls other commands as plain functions, so it must pass every parameter.

A parameter left out keeps its Typer default (an ArgumentInfo/OptionInfo object, not the
value), which crashes inside the command: e.g. `rank` iterating `posting_ids`.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

from autotailor import cli


def test_run_all_passes_every_parameter_to_the_commands_it_calls():
    tree = ast.parse(textwrap.dedent(inspect.getsource(cli.run_all)))
    checked = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        target = getattr(cli, node.func.id, None)
        if not callable(target):
            continue
        params = inspect.signature(target).parameters
        if not any(type(p.default).__name__ in ("ArgumentInfo", "OptionInfo") for p in params.values()):
            continue  # not a Typer command
        passed = {kw.arg for kw in node.keywords}
        missing = [name for name in params if name not in passed]
        assert not missing, f"run_all calls {node.func.id}() without {missing}"
        checked.append(node.func.id)
    assert {"fetch", "screen", "rank", "tailor"} <= set(checked)
