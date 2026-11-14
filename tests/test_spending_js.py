"""The Spending page's client-side filter rules (static/spending.js), run in Node when it is installed."""

import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).parent


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is not installed")
def test_spending_filter_rules():
    script = HERE / "spending_rules.js"
    module = HERE.parent / "fintrack" / "web" / "static" / "spending.js"
    result = subprocess.run(["node", str(script), str(module)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is not installed")
def test_chart_kinds():
    """charts.js's treemap and monthly-bars kinds (colours, dimming of unpicked tiles, tooltips, the average line), with
    stubbed browser globals."""
    script = HERE / "chart_kinds.js"
    module = HERE.parent / "fintrack" / "web" / "static" / "charts.js"
    result = subprocess.run(["node", str(script), str(module)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
