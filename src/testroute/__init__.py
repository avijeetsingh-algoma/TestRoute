"""TestRoute package root.

Component: package root (no component ID; groups C1 to C12 and the new
modules of spec Section 5).

Responsibility:
    Marks ``src/testroute`` as the importable package and exposes the package
    version. Importing this module has no side effects: it does not read
    ``.env``, open files, or start subprocesses, so tests and the sandbox can
    import submodules cheaply.

Receives: nothing.
Produces: ``__version__``, the version string also declared in pyproject.toml.
Talks to: nothing.
Spec sections: 5 (architecture), 12 (repository layout).
Feedback IDs: none directly.
"""

# Kept identical to [project].version in pyproject.toml. Recorded in run
# records so results can be traced to the code version that produced them.
__version__ = "0.1.0"
