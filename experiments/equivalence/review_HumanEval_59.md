# Equivalence review: HumanEval_59 (HumanEval/59, `largest_prime_factor`)

Each mutant below produced the same outputs as the original on every input of differential
execution (179 of 179 inputs usable: HumanEval's own test
inputs plus up to 200 HumanEval+ inputs). Decide for each one, independently, and write the label in
`equivalence_review.csv` (columns reviewer_1, reviewer_2, then final_label and justification).

Labels: EQUIVALENT (no input can tell them apart), NOT_EQUIVALENT (the justification must give a
distinguishing input), UNSURE. Disagreements are settled together and the agreed label goes in final_label.

## Original function

```python


def largest_prime_factor(n: int):
    """Return the largest prime factor of n. Assume n > 1 and is not a prime.
    >>> largest_prime_factor(13195)
    29
    >>> largest_prime_factor(2048)
    2
    """
    def is_prime(k):
        if k < 2:
            return False
        for i in range(2, k - 1):
            if k % i == 0:
                return False
        return True
    largest = 1
    for j in range(2, n + 1):
        if n % j == 0 and is_prime(j):
            largest = max(largest, j)
    return largest
```

## AOR_0024 (AOR: + -> -), line 18

```diff
--- original.py
+++ mutant.py
@@ -17,3 +17,3 @@
     largest = 1
-    for j in range(2, n + 1):
+    for j in range(2, (n - 1)):
         if n % j == 0 and is_prime(j):
```

Differential evidence: 179 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## CON_0003 (CON: 2 -> 1), line 11

```diff
--- original.py
+++ mutant.py
@@ -10,3 +10,3 @@
     def is_prime(k):
-        if k < 2:
+        if k < (1):
             return False
```

Differential evidence: 179 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## CON_0006 (CON: False -> True), line 12

```diff
--- original.py
+++ mutant.py
@@ -11,3 +11,3 @@
         if k < 2:
-            return False
+            return (True)
         for i in range(2, k - 1):
```

Differential evidence: 179 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## CON_0010 (CON: 1 -> 0), line 13

```diff
--- original.py
+++ mutant.py
@@ -12,3 +12,3 @@
             return False
-        for i in range(2, k - 1):
+        for i in range(2, k - (0)):
             if k % i == 0:
```

Differential evidence: 179 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## CON_0020 (CON: 1 -> 0), line 17

```diff
--- original.py
+++ mutant.py
@@ -16,3 +16,3 @@
         return True
-    largest = 1
+    largest = (0)
     for j in range(2, n + 1):
```

Differential evidence: 179 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## CON_0021 (CON: 1 -> 2), line 17

```diff
--- original.py
+++ mutant.py
@@ -16,3 +16,3 @@
         return True
-    largest = 1
+    largest = (2)
     for j in range(2, n + 1):
```

Differential evidence: 179 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## CON_0022 (CON: 2 -> 1), line 18

```diff
--- original.py
+++ mutant.py
@@ -17,3 +17,3 @@
     largest = 1
-    for j in range(2, n + 1):
+    for j in range((1), n + 1):
         if n % j == 0 and is_prime(j):
```

Differential evidence: 179 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## CON_0025 (CON: 1 -> 0), line 18

```diff
--- original.py
+++ mutant.py
@@ -17,3 +17,3 @@
     largest = 1
-    for j in range(2, n + 1):
+    for j in range(2, n + (0)):
         if n % j == 0 and is_prime(j):
```

Differential evidence: 179 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## CON_0026 (CON: 1 -> 2), line 18

```diff
--- original.py
+++ mutant.py
@@ -17,3 +17,3 @@
     largest = 1
-    for j in range(2, n + 1):
+    for j in range(2, n + (2)):
         if n % j == 0 and is_prime(j):
```

Differential evidence: 179 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## RET_0005 (RET: return value -> None), line 12

```diff
--- original.py
+++ mutant.py
@@ -11,3 +11,3 @@
         if k < 2:
-            return False
+            return None
         for i in range(2, k - 1):
```

Differential evidence: 179 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## RET_0016 (RET: return value -> None), line 15

```diff
--- original.py
+++ mutant.py
@@ -14,3 +14,3 @@
             if k % i == 0:
-                return False
+                return None
         return True
```

Differential evidence: 179 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 
