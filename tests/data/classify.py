"""Tiny bundled function under test for the offline integration tests.

It has two decision points and three return paths, so coverage gaps and
surviving mutants are easy to produce on purpose (spec Section 14).
"""


def classify(n):
    """Return "negative", "zero" or "positive" for an integer n."""
    if n < 0:
        return "negative"
    if n == 0:
        return "zero"
    return "positive"
