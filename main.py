"""连点器入口：python main.py 或 pythonw main.py。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from clicker.app import main  # noqa: E402

if __name__ == "__main__":
    main()
