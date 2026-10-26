"""Check installable component metadata and translation consistency."""

import json
from pathlib import Path


def test_manifest_and_translations():
    component = Path(__file__).resolve().parents[1] / "custom_components" / "covau"
    manifest = json.loads((component / "manifest.json").read_text())
    assert manifest["domain"] == "covau"
    assert manifest["config_flow"] is True
    assert manifest["requirements"] == []
    strings = json.loads((component / "strings.json").read_text())
    assert strings == json.loads((component / "translations" / "en.json").read_text())
    assert set(strings["config"]["step"]) == {"user", "account"}
    assert (component / "config_flow.py").is_file()
