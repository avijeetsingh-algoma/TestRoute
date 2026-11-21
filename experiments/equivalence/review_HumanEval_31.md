# Equivalence review: HumanEval_31 (HumanEval/31, `is_prime`)

Each mutant below produced the same outputs as the original on every input of differential
execution (166 of 167 inputs usable: HumanEval's own test
inputs plus up to 200 HumanEval+ inputs). Decide for each one, independently, and write the label in
`equivalence_review.csv` (columns reviewer_1, reviewer_2, then final_label and justification).

Labels: EQUIVALENT (no input can tell them apart), NOT_EQUIVALENT (the justification must give a
distinguishing input), UNSURE. Disagreements are settled together and the agreed label goes in final_label.

## Original function

```python


def is_prime(n):
    """Return true if a given number is prime, and false otherwise.
    >>> is_prime(6)
    False
    >>> is_prime(101)
    True
    >>> is_prime(11)
    True
    >>> is_prime(13441)
    True
    >>> is_prime(61)
    True
    >>> is_prime(4)
    False
    >>> is_prime(1)
    False
    """
    if n < 2:
        return False
    for k in range(2, n - 1):
        if n % k == 0:
            return False
    return True
```

## CON_0010 (CON: 1 -> 0), line 22

```diff
--- original.py
+++ mutant.py
@@ -21,3 +21,3 @@
         return False
-    for k in range(2, n - 1):
+    for k in range(2, n - (0)):
         if n % k == 0:
```

Differential evidence: 166 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 
