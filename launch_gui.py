from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    review_root = Path(__file__).resolve().parent
    scripts_dir = review_root / "scripts"
    sys.path.insert(0, str(scripts_dir))

    from llm_refine_gui import main as gui_main

    return gui_main()


if __name__ == "__main__":
    raise SystemExit(main())
