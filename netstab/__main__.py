import sys

from netstab import splash

if len(sys.argv) >= 3 and sys.argv[1] == "--selftest":
    from netstab.selftest import run

    sys.exit(run(sys.argv[2], no_ping="--no-ping" in sys.argv[3:]))

splash.update("Starting netstab...")
from netstab.gui import main  # noqa: E402 - heavy import; splash shows meanwhile

sys.exit(main())
