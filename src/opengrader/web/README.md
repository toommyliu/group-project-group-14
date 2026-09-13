# @opengrader/web

## Run and build

```sh
uv sync
pnpm --dir src/opengrader/web install --frozen-lockfile
pnpm --dir src/opengrader/web build
uv run opengrader serve # Serves
```

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

