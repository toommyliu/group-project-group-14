# @opengrader/web

## Run and build

```sh
uv sync
npm --prefix src/opengrader/web ci
npm --prefix src/opengrader/web run build
uv run opengrader serve # Serves
```

## Google sign-in

Google sign-in is optional. Set these variables before starting the server to
enable it:

```sh
export OPENGRADER_AUTH_ENABLED=true
export OPENGRADER_GOOGLE_CLIENT_ID="..."
export OPENGRADER_GOOGLE_CLIENT_SECRET="..."
export OPENGRADER_SESSION_SECRET="$(openssl rand -hex 32)"
export OPENGRADER_ADMINS="prof@sjsu.edu"
uv run opengrader serve
```

Only admins and course staff can sign in. Admins, listed in
`OPENGRADER_ADMINS`, create and remove courses and act as an instructor in
every course. Everyone else is added to each course from its People page, as
an instructor or a grader. Instructors manage that course's people and
settings. Graders grade.

The `serve` command also loads these settings from a `.env` file in the
directory where it is launched. Values already exported by the shell take
precedence.

The Google OAuth web client must allow
`http://localhost:8000/auth/callback` as a redirect URI when the server uses
the default port. If `--port` selects another port, register that exact
localhost callback too. Opengrader accepts
verified `sjsu.edu` Workspace accounts by default. Set
`OPENGRADER_GOOGLE_DOMAIN` to use another Workspace domain, and set
`OPENGRADER_SECURE_COOKIE=1` when serving over HTTPS.

Do not commit these values. Without `OPENGRADER_AUTH_ENABLED=true`, Opengrader
keeps its local, signed-out behavior.

## Setting up assignments

An assignment follows the same steps as the sidebar:

1. **Outline.** Upload the blank PDF students write on, or let Opengrader
   generate one from the answer key. Draw a region around each question, plus
   the name and ID fields. The outline sets which questions exist and their
   points. Each template and its regions are stored as a region map, the same
   format the `region-map` CLI writes.
2. **Answer key.** Add expected answers and rubric items to the outline's
   questions.
3. **Scans.** Upload scanned PDFs. Each scan is split every _n_ pages, where
   _n_ is the template's page count. Fix the split, page order, and rotation
   before creating submissions. Creating a submission crops every region from
   its pages. No text is read.
4. **Submissions.** Type each student's name and ID next to the name region.
5. **Grade** each question across the class, then **Review grades** and
   download them as CSV.

Once scans are uploaded, the answer key's structure is locked. On **Grade**,
choose **Edit rubric** to change a question's rubric while grading. **Draft with
AI** proposes partial-credit criteria from student answers using the server's
configured AI provider. It reads scanned answers when needed and reuses their
transcriptions on later requests. Closing the panel keeps the request running
and retains unsaved edits in the current browser tab; reopen it to review the
result. **Cancel AI** stops the request, and **Discard draft** removes unsaved
edits. Reloading the page discards unsaved drafts. Review or edit the draft,
then **Save** to apply it. Saved grades follow renamed or re-scored items; an
item that a saved grade uses cannot be removed. In a class-folder session, **Edit answers and
rubric** also saves changes into the folder's answer key.

The outline's questions are locked once submissions exist. You can still move
the outline's boxes. Saving asks for
confirmation, then crops every submission of that version again from its scan.
Scores, comments, and students picked by hand stay. If the name or ID box
moved, submissions matched automatically are matched to the roster again.

```text
<data home>/assignments/<assignment-id>/
  qa.md
  submissions/
    *.submission.md
    ... answer crops referenced by those files ...
```

## Development

### API contract and checks

FastAPI's models and routes define the network contract. Make sure to re-generate after changing API models or routes to
prevent drift:

```sh
npm --prefix src/opengrader/web run generate:api
npm --prefix src/opengrader/web run check:api
npm --prefix src/opengrader/web run build
```

### Browser flow tests

From the repository root:

```sh
uv sync --group browser
uv run --group browser playwright install chromium --only-shell
npm --prefix src/opengrader/web ci
npm --prefix src/opengrader/web run build
uv run --group browser pytest browser_tests --browser chromium --tracing=retain-on-failure --screenshot=only-on-failure
```

On Linux CI, install browser system libraries with
`uv run --group browser playwright install --with-deps chromium --only-shell`.

These tests run the built website against the real API, using a fresh temporary
course with two small submissions and an automatically assigned localhost port
for every test. They cover PDF scan upload, answer-key import and assignment
creation, decimal scores and feedback across navigation and reloads, live
updates between tabs, failed saves and conflict recovery, and downloaded grade
CSV contents. AI draft flows use a local fake provider to check scanned-answer
transcription, draft review, progress, cancellation, shared grading permissions,
edits made while the AI is running, and safe shutdown during a request.
They do not call external AI providers or run background PDF workers, and do
not use your courses or server home.
Pages and servers shut down after each test.

The default `uv run pytest` continues to run `tests/`; browser tests are an
explicit separate run so unit tests do not require a browser installation.
Failure artifacts go in the ignored `test-results/` directory.
