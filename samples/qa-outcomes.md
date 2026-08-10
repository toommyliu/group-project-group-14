---
title: CS 146 Quiz 3 — Recurrences
instructions: Show your work. Partial credit is given for a correct method.
---

- (10 points) State the recurrence for merge sort and explain each term.
  - **Correct**
    - T(n) = 2T(n/2) + O(n)
  - **Rubric**
    - Correct (+0)
      - **Comment** Correct recurrence with both terms explained.
    - Partially Correct
      - Recurrence right, terms unexplained (-4)
      - Wrong branching factor (-6)
    - Incorrect (-10)
  - **Outcome** CLO2
  - **Outcome** ABET-1

- (6 points) Solve the recurrence two ways.
  - **Outcome** CLO2
  - (3 points) By the Master Theorem.
    - **Correct**
      - O(n log n)
  - (3 points) By a recursion tree.
    - **Correct**
      - O(n log n)
    - **Outcome** CLO3

- (4 points) Which sort is stable?
  - **Choices**
    - A) Merge sort
    - B) Quicksort
    - C) Heapsort
  - **Correct**
    - A
  - **Outcome** CLO1

- (5 points) How many comparisons does merge sort make on 8 elements?
  - **Correct**
    - 24 ±2
  - **Outcome** CLO1, CLO3

- (5 points) Housekeeping: write your student ID in the box.
