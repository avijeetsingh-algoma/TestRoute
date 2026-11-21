# Equivalence review: HumanEval_94 (HumanEval/94, `skjkasdkd`)

Each mutant below produced the same outputs as the original on every input of differential
execution (209 of 209 inputs usable: HumanEval's own test
inputs plus up to 200 HumanEval+ inputs). Decide for each one, independently, and write the label in
`equivalence_review.csv` (columns reviewer_1, reviewer_2, then final_label and justification).

Labels: EQUIVALENT (no input can tell them apart), NOT_EQUIVALENT (the justification must give a
distinguishing input), UNSURE. Disagreements are settled together and the agreed label goes in final_label.

## Original function

```python


def skjkasdkd(lst):
    """You are given a list of integers.
    You need to find the largest prime value and return the sum of its digits.

    Examples:
    For lst = [0,3,2,1,3,5,7,4,5,5,5,2,181,32,4,32,3,2,32,324,4,3] the output should be 10
    For lst = [1,0,1,8,2,4597,2,1,3,40,1,2,1,2,4,2,5,1] the output should be 25
    For lst = [1,3,1,32,5107,34,83278,109,163,23,2323,32,30,1,9,3] the output should be 13
    For lst = [0,724,32,71,99,32,6,0,5,91,83,0,5,6] the output should be 11
    For lst = [0,81,12,3,1,21] the output should be 3
    For lst = [0,8,1,2,1,7] the output should be 7
    """
    def isPrime(n):
        for i in range(2,int(n**0.5)+1):
            if n%i==0:
                return False

        return True
    maxx = 0
    i = 0
    while i < len(lst):
        if(lst[i] > maxx and isPrime(lst[i])):
            maxx = lst[i]
        i+=1
    result = sum(int(digit) for digit in str(maxx))
    return result
```

## CON_0006 (CON: 1 -> 2), line 16

```diff
--- original.py
+++ mutant.py
@@ -15,3 +15,3 @@
     def isPrime(n):
-        for i in range(2,int(n**0.5)+1):
+        for i in range(2,int(n**0.5)+(2)):
             if n%i==0:
```

Differential evidence: 209 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## CON_0015 (CON: 0 -> -1), line 21

```diff
--- original.py
+++ mutant.py
@@ -20,3 +20,3 @@
         return True
-    maxx = 0
+    maxx = (-1)
     i = 0
```

Differential evidence: 209 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## CON_0016 (CON: 0 -> 1), line 21

```diff
--- original.py
+++ mutant.py
@@ -20,3 +20,3 @@
         return True
-    maxx = 0
+    maxx = (1)
     i = 0
```

Differential evidence: 209 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## CON_0017 (CON: 0 -> -1), line 22

```diff
--- original.py
+++ mutant.py
@@ -21,3 +21,3 @@
     maxx = 0
-    i = 0
+    i = (-1)
     while i < len(lst):
```

Differential evidence: 209 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## RET_0011 (RET: return value -> None), line 18

```diff
--- original.py
+++ mutant.py
@@ -17,3 +17,3 @@
             if n%i==0:
-                return False
+                return None
 
```

Differential evidence: 209 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## ROR_0023 (ROR: > -> >=), line 24

```diff
--- original.py
+++ mutant.py
@@ -23,3 +23,3 @@
     while i < len(lst):
-        if(lst[i] > maxx and isPrime(lst[i])):
+        if((lst[i] >= maxx) and isPrime(lst[i])):
             maxx = lst[i]
```

Differential evidence: 209 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 
