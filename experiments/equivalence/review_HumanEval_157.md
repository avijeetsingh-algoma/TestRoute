# Equivalence review: HumanEval_157 (HumanEval/157, `right_angle_triangle`)

Each mutant below produced the same outputs as the original on every input of differential
execution (211 of 211 inputs usable: HumanEval's own test
inputs plus up to 200 HumanEval+ inputs). Decide for each one, independently, and write the label in
`equivalence_review.csv` (columns reviewer_1, reviewer_2, then final_label and justification).

Labels: EQUIVALENT (no input can tell them apart), NOT_EQUIVALENT (the justification must give a
distinguishing input), UNSURE. Disagreements are settled together and the agreed label goes in final_label.

## Original function

```python

def right_angle_triangle(a, b, c):
    '''
    Given the lengths of the three sides of a triangle. Return True if the three
    sides form a right-angled triangle, False otherwise.
    A right-angled triangle is a triangle in which one angle is right angle or 
    90 degree.
    Example:
    right_angle_triangle(3, 4, 5) == True
    right_angle_triangle(1, 2, 3) == False
    '''
    return a*a == b*b + c*c or b*b == a*a + c*c or c*c == a*a + b*b
```

## AOR_0012 (AOR: * -> /), line 12

```diff
--- original.py
+++ mutant.py
@@ -11,2 +11,2 @@
     '''
-    return a*a == b*b + c*c or b*b == a*a + c*c or c*c == a*a + b*b
+    return a*a == b*b + c*c or (b / b) == a*a + c*c or c*c == a*a + b*b
```

Differential evidence: 211 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## AOR_0014 (AOR: * -> +), line 12

```diff
--- original.py
+++ mutant.py
@@ -11,2 +11,2 @@
     '''
-    return a*a == b*b + c*c or b*b == a*a + c*c or c*c == a*a + b*b
+    return a*a == b*b + c*c or b*b == (a + a) + c*c or c*c == a*a + b*b
```

Differential evidence: 211 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## AOR_0015 (AOR: * -> /), line 12

```diff
--- original.py
+++ mutant.py
@@ -11,2 +11,2 @@
     '''
-    return a*a == b*b + c*c or b*b == a*a + c*c or c*c == a*a + b*b
+    return a*a == b*b + c*c or b*b == (a / a) + c*c or c*c == a*a + b*b
```

Differential evidence: 211 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## AOR_0016 (AOR: + -> -), line 12

```diff
--- original.py
+++ mutant.py
@@ -11,2 +11,2 @@
     '''
-    return a*a == b*b + c*c or b*b == a*a + c*c or c*c == a*a + b*b
+    return a*a == b*b + c*c or b*b == (a * a - c * c) or c*c == a*a + b*b
```

Differential evidence: 211 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## AOR_0017 (AOR: * -> +), line 12

```diff
--- original.py
+++ mutant.py
@@ -11,2 +11,2 @@
     '''
-    return a*a == b*b + c*c or b*b == a*a + c*c or c*c == a*a + b*b
+    return a*a == b*b + c*c or b*b == a*a + (c + c) or c*c == a*a + b*b
```

Differential evidence: 211 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## AOR_0018 (AOR: * -> /), line 12

```diff
--- original.py
+++ mutant.py
@@ -11,2 +11,2 @@
     '''
-    return a*a == b*b + c*c or b*b == a*a + c*c or c*c == a*a + b*b
+    return a*a == b*b + c*c or b*b == a*a + (c / c) or c*c == a*a + b*b
```

Differential evidence: 211 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 
