"""Compatibility entry point; implementation: json2scapi.pipeline.regression."""
import importlib
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_impl = importlib.import_module("json2scapi.pipeline.regression")

if __name__ == "__main__":
    raise SystemExit(_impl.main())
else:
    sys.modules[__name__] = _impl
