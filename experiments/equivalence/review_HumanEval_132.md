# Equivalence review: HumanEval_132 (HumanEval/132, `is_nested`)

Each mutant below produced the same outputs as the original on every input of differential
execution (214 of 214 inputs usable: HumanEval's own test
inputs plus up to 200 HumanEval+ inputs). Decide for each one, independently, and write the label in
`equivalence_review.csv` (columns reviewer_1, reviewer_2, then final_label and justification).

Labels: EQUIVALENT (no input can tell them apart), NOT_EQUIVALENT (the justification must give a
distinguishing input), UNSURE. Disagreements are settled together and the agreed label goes in final_label.

## Original function

```python

def is_nested(string):
    '''
    Create a function that takes a string as input which contains only square brackets.
    The function should return True if and only if there is a valid subsequence of brackets 
    where at least one bracket in the subsequence is nested.

    is_nested('[[]]') ➞ True
    is_nested('[]]]]]]][[[[[]') ➞ False
    is_nested('[][]') ➞ False
    is_nested('[]') ➞ False
    is_nested('[[][]]') ➞ True
    is_nested('[[]][[') ➞ True
    '''
    opening_bracket_index = []
    closing_bracket_index = []
    for i in range(len(string)):
        if string[i] == '[':
            opening_bracket_index.append(i)
        else:
            closing_bracket_index.append(i)
    closing_bracket_index.reverse()
    cnt = 0
    i = 0
    l = len(closing_bracket_index)
    for idx in opening_bracket_index:
        if i < l and idx < closing_bracket_index[i]:
            cnt += 1
            i += 1
    return cnt >= 2

    
```

## ROR_0009 (ROR: < -> <=), line 27

```diff
--- original.py
+++ mutant.py
@@ -26,3 +26,3 @@
     for idx in opening_bracket_index:
-        if i < l and idx < closing_bracket_index[i]:
+        if i < l and (idx <= closing_bracket_index[i]):
             cnt += 1
```

Differential evidence: 214 inputs compared, all outputs and argument states equal.

- reviewer_1: 
- reviewer_2: 
- final_label: 
- justification: 
