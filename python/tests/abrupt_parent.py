"""Start a managed host and exit without Python teardown to exercise Job Object ownership."""

from __future__ import annotations

import os
from pathlib import Path
import sys

from dnp3_master import Dnp3MasterClient, HostProcessConfig


def main() -> None:
    client = Dnp3MasterClient(
        HostProcessConfig(
            executable=Path(sys.argv[1]),
            startup_timeout=2.0,
            shutdown_timeout=1.0,
        )
    )
    client.start()
    print(client.pid, flush=True)
    os._exit(91)


if __name__ == "__main__":
    main()
