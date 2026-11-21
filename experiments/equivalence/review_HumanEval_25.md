# Equivalence review: HumanEval_25 (HumanEval/25, `factorize`)

Each mutant below produced the same outputs as the original on every input of differential
execution (146 of 146 inputs usable: HumanEval's own test
inputs plus up to 200 HumanEval+ inputs). Decide for each one, independently, and write the label in
`equivalence_review.csv` (columns reviewer_1, reviewer_2, then final_label and justification).

Labels: EQUIVALENT (no input can tell them apart), NOT_EQUIVALENT (the justification must give a
distinguishing input), UNSURE. Disagreements are settled together and the agreed label goes in final_label.

## Original function

```python
from typing import List


def factorize(n: int) -> List[int]:
    """ Return list of prime factors of given integer in the order from smallest to largest.
    Each of the factors should be listed number of times corresponding to how many times it appeares in factorization.
    Input number should be equal to the product of all factors
    >>> factorize(8)
    [2, 2, 2]
    >>> factorize(25)
    [5, 5]
    >>> factorize(70)
    [2, 5, 7]
    """
    import math
    fact = []
    i = 2
    while i <= int(math.sqrt(n) + 1):
        if n % i == 0:
            fact.append(i)
            n //= i
        else:
            i += 1

    if n > 1:
        fact.append(n)
    return fact
```

## CON_0006 (CON: 1 -> 0), line 18

```diff
--- original.py
+++ mutant.py
@@ -17,3 +17,3 @@
     i = 2
-    while i <= int(math.sqrt(n) + 1):
+    while i <= int(math.sqrt(n) + (0)):
         if n % i == 0:
```

Differential evidence: 146 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## CON_0007 (CON: 1 -> 2), line 18

```diff
--- original.py
+++ mutant.py
@@ -17,3 +17,3 @@
     i = 2
-    while i <= int(math.sqrt(n) + 1):
+    while i <= int(math.sqrt(n) + (2)):
         if n % i == 0:
```

Differential evidence: 146 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## CON_0020 (CON: 1 -> 2), line 25

```diff
--- original.py
+++ mutant.py
@@ -24,3 +24,3 @@
 
-    if n > 1:
+    if n > (2):
         fact.append(n)
```

Differential evidence: 146 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## ROR_0003 (ROR: <= -> <), line 18

```diff
--- original.py
+++ mutant.py
@@ -17,3 +17,3 @@
     i = 2
-    while i <= int(math.sqrt(n) + 1):
+    while (i < int(math.sqrt(n) + 1)):
         if n % i == 0:
```

Differential evidence: 146 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 
