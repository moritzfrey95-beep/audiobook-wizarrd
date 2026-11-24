"""Eine Versionsnummer für Backend, Oberfläche und Image."""
import json
import re
from pathlib import Path

from abw import __version__

ROOT = Path(__file__).resolve().parents[2]


def test_version_ueberall_gleich():
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__)
    assert json.loads((ROOT / "frontend/package.json").read_text())["version"] == __version__
    assert f'org.opencontainers.image.version="{__version__}"' in (ROOT / "Dockerfile").read_text()
