"""Module entry point so ``python -m testroute <command>`` works.

Component: new module support for ``cli.py`` (spec Section 5).

Responsibility:
    Hands control to :func:`testroute.cli.main` and turns its integer return
    value into the process exit code, which the ``.bat`` launchers check.

Receives: command-line arguments (through ``sys.argv``, read by ``cli.main``).
Produces: the process exit code.
Talks to: ``testroute.cli``.
Spec sections: 5, 12.
Feedback IDs: FB9 (single automated entry point).
"""

import sys

from testroute.cli import main

if __name__ == "__main__":
    sys.exit(main())
