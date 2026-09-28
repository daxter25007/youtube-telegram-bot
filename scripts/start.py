from __future__ import annotations

import os
import subprocess
import sys


def main() -> None:
    subprocess.run(["alembic", "upgrade", "head"], check=True)  # noqa: S607
    os.execv(sys.executable, [sys.executable, "-m", "app.main"])  # noqa: S606


if __name__ == "__main__":
    main()
