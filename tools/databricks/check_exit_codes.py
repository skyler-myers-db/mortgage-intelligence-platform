"""Exit codes shared by the read-only converger ``--check`` and ``diff`` modes.

A pre-stop probe has to tell three outcomes apart: the live state already
equals the reviewed state; convergence (or the App update) would change it;
the probe itself could not decide. Python exits 1 on an uncaught exception and
argparse exits 2 on a usage error, so drift has a code of its own and a failed
probe can never read as "converge after the stop".
"""

from __future__ import annotations

CHECK_EXACT = 0
CHECK_DRIFT = 3
