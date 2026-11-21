# Equivalence review: HumanEval_128 (HumanEval/128, `prod_signs`)

Each mutant below produced the same outputs as the original on every input of differential
execution (208 of 208 inputs usable: HumanEval's own test
inputs plus up to 200 HumanEval+ inputs). Decide for each one, independently, and write the label in
`equivalence_review.csv` (columns reviewer_1, reviewer_2, then final_label and justification).

Labels: EQUIVALENT (no input can tell them apart), NOT_EQUIVALENT (the justification must give a
distinguishing input), UNSURE. Disagreements are settled together and the agreed label goes in final_label.

## Original function

```python

def prod_signs(arr):
    """
    You are given an array arr of integers and you need to return
    sum of magnitudes of integers multiplied by product of all signs
    of each number in the array, represented by 1, -1 or 0.
    Note: return None for empty arr.

    Example:
    >>> prod_signs([1, 2, 2, -4]) == -9
    >>> prod_signs([0, 1]) == 0
    >>> prod_signs([]) == None
    """
    if not arr: return None
    prod = 0 if 0 in arr else (-1) ** len(list(filter(lambda x: x < 0, arr)))
    return prod * sum([abs(i) for i in arr])
```

## CON_0013 (CON: 0 -> 1), line 15

```diff
--- original.py
+++ mutant.py
@@ -14,3 +14,3 @@
     if not arr: return None
-    prod = 0 if 0 in arr else (-1) ** len(list(filter(lambda x: x < 0, arr)))
+    prod = 0 if 0 in arr else (-1) ** len(list(filter(lambda x: x < (1), arr)))
     return prod * sum([abs(i) for i in arr])
```

Differential evidence: 208 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## ROR_0010 (ROR: < -> <=), line 15

```diff
--- original.py
+++ mutant.py
@@ -14,3 +14,3 @@
     if not arr: return None
-    prod = 0 if 0 in arr else (-1) ** len(list(filter(lambda x: x < 0, arr)))
+    prod = 0 if 0 in arr else (-1) ** len(list(filter(lambda x: (x <= 0), arr)))
     return prod * sum([abs(i) for i in arr])
```

Differential evidence: 208 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 
