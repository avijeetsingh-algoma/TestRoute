# Equivalence review: HumanEval_124 (HumanEval/124, `valid_date`)

Each mutant below produced the same outputs as the original on every input of differential
execution (216 of 216 inputs usable: HumanEval's own test
inputs plus up to 200 HumanEval+ inputs). Decide for each one, independently, and write the label in
`equivalence_review.csv` (columns reviewer_1, reviewer_2, then final_label and justification).

Labels: EQUIVALENT (no input can tell them apart), NOT_EQUIVALENT (the justification must give a
distinguishing input), UNSURE. Disagreements are settled together and the agreed label goes in final_label.

## Original function

```python

def valid_date(date):
    """You have to write a function which validates a given date string and
    returns True if the date is valid otherwise False.
    The date is valid if all of the following rules are satisfied:
    1. The date string is not empty.
    2. The number of days is not less than 1 or higher than 31 days for months 1,3,5,7,8,10,12. And the number of days is not less than 1 or higher than 30 days for months 4,6,9,11. And, the number of days is not less than 1 or higher than 29 for the month 2.
    3. The months should not be less than 1 or higher than 12.
    4. The date should be in the format: mm-dd-yyyy

    for example: 
    valid_date('03-11-2000') => True

    valid_date('15-01-2012') => False

    valid_date('04-0-2040') => False

    valid_date('06-04-2020') => True

    valid_date('06/04/2020') => False
    """
    try:
        date = date.strip()
        month, day, year = date.split('-')
        month, day, year = int(month), int(day), int(year)
        if month < 1 or month > 12:
            return False
        if month in [1,3,5,7,8,10,12] and day < 1 or day > 31:
            return False
        if month in [4,6,9,11] and day < 1 or day > 30:
            return False
        if month == 2 and day < 1 or day > 29:
            return False
    except:
        return False

    return True
```

## CON_0008 (CON: 12 -> 11), line 26

```diff
--- original.py
+++ mutant.py
@@ -25,3 +25,3 @@
         month, day, year = int(month), int(day), int(year)
-        if month < 1 or month > 12:
+        if month < 1 or month > (11):
             return False
```

Differential evidence: 216 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## CON_0009 (CON: 12 -> 13), line 26

```diff
--- original.py
+++ mutant.py
@@ -25,3 +25,3 @@
         month, day, year = int(month), int(day), int(year)
-        if month < 1 or month > 12:
+        if month < 1 or month > (13):
             return False
```

Differential evidence: 216 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## CON_0017 (CON: 3 -> 2), line 28

```diff
--- original.py
+++ mutant.py
@@ -27,3 +27,3 @@
             return False
-        if month in [1,3,5,7,8,10,12] and day < 1 or day > 31:
+        if month in [1,(2),5,7,8,10,12] and day < 1 or day > 31:
             return False
```

Differential evidence: 216 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## CON_0018 (CON: 3 -> 4), line 28

```diff
--- original.py
+++ mutant.py
@@ -27,3 +27,3 @@
             return False
-        if month in [1,3,5,7,8,10,12] and day < 1 or day > 31:
+        if month in [1,(4),5,7,8,10,12] and day < 1 or day > 31:
             return False
```

Differential evidence: 216 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## CON_0019 (CON: 5 -> 4), line 28

```diff
--- original.py
+++ mutant.py
@@ -27,3 +27,3 @@
             return False
-        if month in [1,3,5,7,8,10,12] and day < 1 or day > 31:
+        if month in [1,3,(4),7,8,10,12] and day < 1 or day > 31:
             return False
```

Differential evidence: 216 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## CON_0020 (CON: 5 -> 6), line 28

```diff
--- original.py
+++ mutant.py
@@ -27,3 +27,3 @@
             return False
-        if month in [1,3,5,7,8,10,12] and day < 1 or day > 31:
+        if month in [1,3,(6),7,8,10,12] and day < 1 or day > 31:
             return False
```

Differential evidence: 216 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## CON_0021 (CON: 7 -> 6), line 28

```diff
--- original.py
+++ mutant.py
@@ -27,3 +27,3 @@
             return False
-        if month in [1,3,5,7,8,10,12] and day < 1 or day > 31:
+        if month in [1,3,5,(6),8,10,12] and day < 1 or day > 31:
             return False
```

Differential evidence: 216 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## CON_0022 (CON: 7 -> 8), line 28

```diff
--- original.py
+++ mutant.py
@@ -27,3 +27,3 @@
             return False
-        if month in [1,3,5,7,8,10,12] and day < 1 or day > 31:
+        if month in [1,3,5,(8),8,10,12] and day < 1 or day > 31:
             return False
```

Differential evidence: 216 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## CON_0023 (CON: 8 -> 7), line 28

```diff
--- original.py
+++ mutant.py
@@ -27,3 +27,3 @@
             return False
-        if month in [1,3,5,7,8,10,12] and day < 1 or day > 31:
+        if month in [1,3,5,7,(7),10,12] and day < 1 or day > 31:
             return False
```

Differential evidence: 216 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## CON_0024 (CON: 8 -> 9), line 28

```diff
--- original.py
+++ mutant.py
@@ -27,3 +27,3 @@
             return False
-        if month in [1,3,5,7,8,10,12] and day < 1 or day > 31:
+        if month in [1,3,5,7,(9),10,12] and day < 1 or day > 31:
             return False
```

Differential evidence: 216 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## CON_0025 (CON: 10 -> 11), line 28

```diff
--- original.py
+++ mutant.py
@@ -27,3 +27,3 @@
             return False
-        if month in [1,3,5,7,8,10,12] and day < 1 or day > 31:
+        if month in [1,3,5,7,8,(11),12] and day < 1 or day > 31:
             return False
```

Differential evidence: 216 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## CON_0026 (CON: 10 -> 9), line 28

```diff
--- original.py
+++ mutant.py
@@ -27,3 +27,3 @@
             return False
-        if month in [1,3,5,7,8,10,12] and day < 1 or day > 31:
+        if month in [1,3,5,7,8,(9),12] and day < 1 or day > 31:
             return False
```

Differential evidence: 216 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## CON_0027 (CON: 12 -> 11), line 28

```diff
--- original.py
+++ mutant.py
@@ -27,3 +27,3 @@
             return False
-        if month in [1,3,5,7,8,10,12] and day < 1 or day > 31:
+        if month in [1,3,5,7,8,10,(11)] and day < 1 or day > 31:
             return False
```

Differential evidence: 216 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## CON_0028 (CON: 12 -> 13), line 28

```diff
--- original.py
+++ mutant.py
@@ -27,3 +27,3 @@
             return False
-        if month in [1,3,5,7,8,10,12] and day < 1 or day > 31:
+        if month in [1,3,5,7,8,10,(13)] and day < 1 or day > 31:
             return False
```

Differential evidence: 216 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## CON_0035 (CON: 31 -> 30), line 28

```diff
--- original.py
+++ mutant.py
@@ -27,3 +27,3 @@
             return False
-        if month in [1,3,5,7,8,10,12] and day < 1 or day > 31:
+        if month in [1,3,5,7,8,10,12] and day < 1 or day > (30):
             return False
```

Differential evidence: 216 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## CON_0036 (CON: 31 -> 32), line 28

```diff
--- original.py
+++ mutant.py
@@ -27,3 +27,3 @@
             return False
-        if month in [1,3,5,7,8,10,12] and day < 1 or day > 31:
+        if month in [1,3,5,7,8,10,12] and day < 1 or day > (32):
             return False
```

Differential evidence: 216 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## CON_0044 (CON: 6 -> 5), line 30

```diff
--- original.py
+++ mutant.py
@@ -29,3 +29,3 @@
             return False
-        if month in [4,6,9,11] and day < 1 or day > 30:
+        if month in [4,(5),9,11] and day < 1 or day > 30:
             return False
```

Differential evidence: 216 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## CON_0045 (CON: 6 -> 7), line 30

```diff
--- original.py
+++ mutant.py
@@ -29,3 +29,3 @@
             return False
-        if month in [4,6,9,11] and day < 1 or day > 30:
+        if month in [4,(7),9,11] and day < 1 or day > 30:
             return False
```

Differential evidence: 216 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## CON_0046 (CON: 9 -> 10), line 30

```diff
--- original.py
+++ mutant.py
@@ -29,3 +29,3 @@
             return False
-        if month in [4,6,9,11] and day < 1 or day > 30:
+        if month in [4,6,(10),11] and day < 1 or day > 30:
             return False
```

Differential evidence: 216 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## CON_0047 (CON: 9 -> 8), line 30

```diff
--- original.py
+++ mutant.py
@@ -29,3 +29,3 @@
             return False
-        if month in [4,6,9,11] and day < 1 or day > 30:
+        if month in [4,6,(8),11] and day < 1 or day > 30:
             return False
```

Differential evidence: 216 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## CON_0048 (CON: 11 -> 10), line 30

```diff
--- original.py
+++ mutant.py
@@ -29,3 +29,3 @@
             return False
-        if month in [4,6,9,11] and day < 1 or day > 30:
+        if month in [4,6,9,(10)] and day < 1 or day > 30:
             return False
```

Differential evidence: 216 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## CON_0049 (CON: 11 -> 12), line 30

```diff
--- original.py
+++ mutant.py
@@ -29,3 +29,3 @@
             return False
-        if month in [4,6,9,11] and day < 1 or day > 30:
+        if month in [4,6,9,(12)] and day < 1 or day > 30:
             return False
```

Differential evidence: 216 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## CON_0053 (CON: 1 -> 2), line 30

```diff
--- original.py
+++ mutant.py
@@ -29,3 +29,3 @@
             return False
-        if month in [4,6,9,11] and day < 1 or day > 30:
+        if month in [4,6,9,11] and day < (2) or day > 30:
             return False
```

Differential evidence: 216 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## CON_0056 (CON: 30 -> 29), line 30

```diff
--- original.py
+++ mutant.py
@@ -29,3 +29,3 @@
             return False
-        if month in [4,6,9,11] and day < 1 or day > 30:
+        if month in [4,6,9,11] and day < 1 or day > (29):
             return False
```

Differential evidence: 216 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## CON_0057 (CON: 30 -> 31), line 30

```diff
--- original.py
+++ mutant.py
@@ -29,3 +29,3 @@
             return False
-        if month in [4,6,9,11] and day < 1 or day > 30:
+        if month in [4,6,9,11] and day < 1 or day > (31):
             return False
```

Differential evidence: 216 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## CON_0063 (CON: 2 -> 1), line 32

```diff
--- original.py
+++ mutant.py
@@ -31,3 +31,3 @@
             return False
-        if month == 2 and day < 1 or day > 29:
+        if month == (1) and day < 1 or day > 29:
             return False
```

Differential evidence: 216 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## CON_0064 (CON: 2 -> 3), line 32

```diff
--- original.py
+++ mutant.py
@@ -31,3 +31,3 @@
             return False
-        if month == 2 and day < 1 or day > 29:
+        if month == (3) and day < 1 or day > 29:
             return False
```

Differential evidence: 216 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## CON_0067 (CON: 1 -> 0), line 32

```diff
--- original.py
+++ mutant.py
@@ -31,3 +31,3 @@
             return False
-        if month == 2 and day < 1 or day > 29:
+        if month == 2 and day < (0) or day > 29:
             return False
```

Differential evidence: 216 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## CON_0068 (CON: 1 -> 2), line 32

```diff
--- original.py
+++ mutant.py
@@ -31,3 +31,3 @@
             return False
-        if month == 2 and day < 1 or day > 29:
+        if month == 2 and day < (2) or day > 29:
             return False
```

Differential evidence: 216 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## CON_0071 (CON: 29 -> 28), line 32

```diff
--- original.py
+++ mutant.py
@@ -31,3 +31,3 @@
             return False
-        if month == 2 and day < 1 or day > 29:
+        if month == 2 and day < 1 or day > (28):
             return False
```

Differential evidence: 216 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## CON_0072 (CON: 29 -> 30), line 32

```diff
--- original.py
+++ mutant.py
@@ -31,3 +31,3 @@
             return False
-        if month == 2 and day < 1 or day > 29:
+        if month == 2 and day < 1 or day > (30):
             return False
```

Differential evidence: 216 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## CON_0074 (CON: False -> True), line 33

```diff
--- original.py
+++ mutant.py
@@ -32,3 +32,3 @@
         if month == 2 and day < 1 or day > 29:
-            return False
+            return (True)
     except:
```

Differential evidence: 216 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## COR_0060 (COR: and -> or), line 32

```diff
--- original.py
+++ mutant.py
@@ -31,3 +31,3 @@
             return False
-        if month == 2 and day < 1 or day > 29:
+        if (month == 2 or day < 1) or day > 29:
             return False
```

Differential evidence: 216 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## COR_0061 (COR: or -> and), line 32

```diff
--- original.py
+++ mutant.py
@@ -31,3 +31,3 @@
             return False
-        if month == 2 and day < 1 or day > 29:
+        if ((month == 2 and day < 1) and day > 29):
             return False
```

Differential evidence: 216 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## RET_0073 (RET: return value -> None), line 33

```diff
--- original.py
+++ mutant.py
@@ -32,3 +32,3 @@
         if month == 2 and day < 1 or day > 29:
-            return False
+            return None
     except:
```

Differential evidence: 216 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## ROR_0007 (ROR: > -> >=), line 26

```diff
--- original.py
+++ mutant.py
@@ -25,3 +25,3 @@
         month, day, year = int(month), int(day), int(year)
-        if month < 1 or month > 12:
+        if month < 1 or (month >= 12):
             return False
```

Differential evidence: 216 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## ROR_0034 (ROR: > -> >=), line 28

```diff
--- original.py
+++ mutant.py
@@ -27,3 +27,3 @@
             return False
-        if month in [1,3,5,7,8,10,12] and day < 1 or day > 31:
+        if month in [1,3,5,7,8,10,12] and day < 1 or (day >= 31):
             return False
```

Differential evidence: 216 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## ROR_0050 (ROR: < -> <=), line 30

```diff
--- original.py
+++ mutant.py
@@ -29,3 +29,3 @@
             return False
-        if month in [4,6,9,11] and day < 1 or day > 30:
+        if month in [4,6,9,11] and (day <= 1) or day > 30:
             return False
```

Differential evidence: 216 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## ROR_0055 (ROR: > -> >=), line 30

```diff
--- original.py
+++ mutant.py
@@ -29,3 +29,3 @@
             return False
-        if month in [4,6,9,11] and day < 1 or day > 30:
+        if month in [4,6,9,11] and day < 1 or (day >= 30):
             return False
```

Differential evidence: 216 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## ROR_0062 (ROR: == -> !=), line 32

```diff
--- original.py
+++ mutant.py
@@ -31,3 +31,3 @@
             return False
-        if month == 2 and day < 1 or day > 29:
+        if (month != 2) and day < 1 or day > 29:
             return False
```

Differential evidence: 216 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## ROR_0065 (ROR: < -> <=), line 32

```diff
--- original.py
+++ mutant.py
@@ -31,3 +31,3 @@
             return False
-        if month == 2 and day < 1 or day > 29:
+        if month == 2 and (day <= 1) or day > 29:
             return False
```

Differential evidence: 216 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## ROR_0066 (ROR: < -> >), line 32

```diff
--- original.py
+++ mutant.py
@@ -31,3 +31,3 @@
             return False
-        if month == 2 and day < 1 or day > 29:
+        if month == 2 and (day > 1) or day > 29:
             return False
```

Differential evidence: 216 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## ROR_0070 (ROR: > -> >=), line 32

```diff
--- original.py
+++ mutant.py
@@ -31,3 +31,3 @@
             return False
-        if month == 2 and day < 1 or day > 29:
+        if month == 2 and day < 1 or (day >= 29):
             return False
```

Differential evidence: 216 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 
