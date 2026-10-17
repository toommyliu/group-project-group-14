# Writing an answer key for Opengrader

You are helping a teacher with `qa.md`, the answer key Opengrader grades
with. This folder holds no student work.

**Your task:** {task}

Run Opengrader as:

```
{og}
```

Below, `og` stands for that whole command.

## Steps

1. Read `qa-format.md` (the format, with examples) and the files named above.
2. If there is no `qa.md` yet, you may start from Opengrader's own draft and
   fix it: `og create-answer-key --input "{source}" --output qa.md`. The draft
   is often incomplete. Check every question against the source.
3. Every question needs its points, its correct answer(s), and a rubric with
   Correct, Partially Correct and Incorrect. When `qa.md` is already there,
   change only what your task asks for, and keep everything else as it is.
   For partial credit with several options (a checklist), the options must
   add up to the question's full points (for awards) or full deduction (for
   deductions).
4. Run `og agent check-key qa.md` and fix every **error**. Then read each
   **warning**: fix it, or tell the teacher why it is intended.
5. Do not guess. If nothing settles a question's answer, or two sources
   disagree, leave the answer blank and tell the teacher.

## When you are done

Tell the teacher what you wrote, which questions you were unsure of, and
any warnings left. They copy the key into their class folder themselves.

Write only inside this folder.
