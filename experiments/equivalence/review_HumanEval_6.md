# Equivalence review: HumanEval_6 (HumanEval/6, `parse_nested_parens`)

Each mutant below produced the same outputs as the original on every input of differential
execution (69 of 69 inputs usable: HumanEval's own test
inputs plus up to 200 HumanEval+ inputs). Decide for each one, independently, and write the label in
`equivalence_review.csv` (columns reviewer_1, reviewer_2, then final_label and justification).

Labels: EQUIVALENT (no input can tell them apart), NOT_EQUIVALENT (the justification must give a
distinguishing input), UNSURE. Disagreements are settled together and the agreed label goes in final_label.

## Original function

```python
from typing import List


def parse_nested_parens(paren_string: str) -> List[int]:
    """ Input to this function is a string represented multiple groups for nested parentheses separated by spaces.
    For each of the group, output the deepest level of nesting of parentheses.
    E.g. (()()) has maximum two levels of nesting while ((())) has three.

    >>> parse_nested_parens('(()()) ((())) () ((())()())')
    [2, 3, 1, 3]
    """
    def parse_paren_group(s):
        depth = 0
        max_depth = 0
        for c in s:
            if c == '(':
                depth += 1
                max_depth = max(depth, max_depth)
            else:
                depth -= 1

        return max_depth

    return [parse_paren_group(x) for x in paren_string.split(' ') if x]
```

## CON_0003 (CON: 0 -> -1), line 14

```diff
--- original.py
+++ mutant.py
@@ -13,3 +13,3 @@
         depth = 0
-        max_depth = 0
+        max_depth = (-1)
         for c in s:
```

Differential evidence: 69 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 

## CON_0004 (CON: 0 -> 1), line 14

```diff
--- original.py
+++ mutant.py
@@ -13,3 +13,3 @@
         depth = 0
-        max_depth = 0
+        max_depth = (1)
         for c in s:
```

Differential evidence: 69 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 
