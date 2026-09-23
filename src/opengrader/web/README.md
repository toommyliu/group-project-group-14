# @opengrader/web

## Run and build

```sh
uv sync
pnpm --dir src/opengrader/web install --frozen-lockfile
pnpm --dir src/opengrader/web build
uv run opengrader serve # Serves
```

## Google sign-in

Google sign-in is optional. Set all three required variables before starting the
server to enable it:

```sh
export OPENGRADER_GOOGLE_CLIENT_ID="..."
export OPENGRADER_GOOGLE_CLIENT_SECRET="..."
export OPENGRADER_SESSION_SECRET="$(openssl rand -hex 32)"
uv run opengrader serve
```

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

Do not commit these values. If the variables are absent, Opengrader keeps its
existing local, signed-out behavior.

## Setting up assignments

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
pnpm --dir src/opengrader/web generate:api
pnpm --dir src/opengrader/web check:api
pnpm --dir src/opengrader/web build
```
