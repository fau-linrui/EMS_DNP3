from __future__ import annotations

import argparse
import importlib.metadata
import json
from pathlib import Path
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("environment", "installed-package"))
    arguments = parser.parse_args()
    if arguments.mode == "environment":
        try:
            setuptools_version: str | None = importlib.metadata.version("setuptools")
        except importlib.metadata.PackageNotFoundError:
            setuptools_version = None
        result = {
            "python": ".".join(map(str, sys.version_info[:3])),
            "pytest": importlib.metadata.version("pytest"),
            "pip": importlib.metadata.version("pip"),
            "setuptools": setuptools_version,
        }
    else:
        import dnp3_master

        result = {
            "path": str(Path(dnp3_master.__file__).resolve()),
            "version": dnp3_master.__version__,
        }
    # Keep JSON portable across Windows pipe code pages; parsers restore the
    # original Unicode path from escapes without relying on PYTHONIOENCODING.
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
