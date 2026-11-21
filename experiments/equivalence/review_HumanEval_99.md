# Equivalence review: HumanEval_99 (HumanEval/99, `closest_integer`)

Each mutant below produced the same outputs as the original on every input of differential
execution (205 of 205 inputs usable: HumanEval's own test
inputs plus up to 200 HumanEval+ inputs). Decide for each one, independently, and write the label in
`equivalence_review.csv` (columns reviewer_1, reviewer_2, then final_label and justification).

Labels: EQUIVALENT (no input can tell them apart), NOT_EQUIVALENT (the justification must give a
distinguishing input), UNSURE. Disagreements are settled together and the agreed label goes in final_label.

## Original function

```python

def closest_integer(value):
    '''
    Create a function that takes a value (string) representing a number
    and returns the closest integer to it. If the number is equidistant
    from two integers, round it away from zero.

    Examples
    >>> closest_integer("10")
    10
    >>> closest_integer("15.3")
    15

    Note:
    Rounding away from zero means that if the given number is equidistant
    from two integers, the one you should return is the one that is the
    farthest from zero. For example closest_integer("14.5") should
    return 15 and closest_integer("-14.5") should return -15.
    '''
    from math import floor, ceil

    if value.count('.') == 1:
        # remove trailing zeros
        while (value[-1] == '0'):
            value = value[:-1]

    num = float(value)
    if value[-2:] == '.5':
        if num > 0:
            res = ceil(num)
        else:
            res = floor(num)
    elif len(value) > 0:
        res = int(round(num))
    else:
        res = 0

    return res
```

## CON_0003 (CON: 1 -> 2), line 22

```diff
--- original.py
+++ mutant.py
@@ -21,3 +21,3 @@
 
-    if value.count('.') == 1:
+    if value.count('.') == (2):
         # remove trailing zeros
```

Differential evidence: 205 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## CON_0014 (CON: 0 -> -1), line 29

```diff
--- original.py
+++ mutant.py
@@ -28,3 +28,3 @@
     if value[-2:] == '.5':
-        if num > 0:
+        if num > (-1):
             res = ceil(num)
```

Differential evidence: 205 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## CON_0018 (CON: 0 -> -1), line 33

```diff
--- original.py
+++ mutant.py
@@ -32,3 +32,3 @@
             res = floor(num)
-    elif len(value) > 0:
+    elif len(value) > (-1):
         res = int(round(num))
```

Differential evidence: 205 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## CON_0020 (CON: 0 -> -1), line 36

```diff
--- original.py
+++ mutant.py
@@ -35,3 +35,3 @@
     else:
-        res = 0
+        res = (-1)
 
```

Differential evidence: 205 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## CON_0021 (CON: 0 -> 1), line 36

```diff
--- original.py
+++ mutant.py
@@ -35,3 +35,3 @@
     else:
-        res = 0
+        res = (1)
 
```

Differential evidence: 205 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## ROR_0013 (ROR: > -> >=), line 29

```diff
--- original.py
+++ mutant.py
@@ -28,3 +28,3 @@
     if value[-2:] == '.5':
-        if num > 0:
+        if (num >= 0):
             res = ceil(num)
```

Differential evidence: 205 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## ROR_0017 (ROR: > -> >=), line 33

```diff
--- original.py
+++ mutant.py
@@ -32,3 +32,3 @@
             res = floor(num)
-    elif len(value) > 0:
+    elif (len(value) >= 0):
         res = int(round(num))
```

Differential evidence: 205 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 
