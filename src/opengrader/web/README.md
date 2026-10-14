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

The answer key is locked once scans are uploaded, and the outline's questions
once submissions exist. You can still move the outline's boxes. Saving asks for
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
