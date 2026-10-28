"""Repository resources resolved independently of cwd and module layout."""
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parents[1]
WORKSPACE_DIR = PACKAGE_DIR.parent
SCHEMA_PATH = PACKAGE_DIR / "schema" / "ir-0.1.schema.json"
OUT_DIR = PACKAGE_DIR / "out"
REFERENCE_SCRIPT = WORKSPACE_DIR / "deepcad_official" / "local_adapter" / "json2step_ocp.py"
