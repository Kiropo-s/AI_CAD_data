"""Run from any working directory with the existing CAD Python environment."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from step2code_direct.cli import main
if __name__ == '__main__':
    raise SystemExit(main())
