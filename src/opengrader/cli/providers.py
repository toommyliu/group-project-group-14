from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import click

from opengrader.cli import CONTEXT_SETTINGS, Group
from opengrader.core import app_config
from opengrader.core.app_config import manifests as ai_manifests

EPILOG = """\
\b
Examples:
  uv run opengrader-providers list
  uv run opengrader-providers list --json
  uv run opengrader-providers add --example openai
  uv run opengrader-providers add --name sjsu-gateway --label "SJSU Gateway" \\
      --dialect anthropic-messages --host https://ai.example.edu/v1 \\
      --vision-model claude-haiku-4-5 --text-model claude-haiku-4-5 \\
      --api-key-env SJSU_AI_KEY
  uv run opengrader-providers add --from ~/Downloads/campus-gateway.toml
  uv run opengrader-providers validate
  uv run opengrader-providers validate openai
  uv run opengrader-providers test openai --yes
  uv run opengrader-providers remove sjsu-gateway --yes
"""

_TEST_PROMPT = ai_manifests.CONNECTION_TEST_PROMPT


def _error(message: str) -> None:
    print(f"Error: {message}", file=sys.stderr)


def _consent_summary(spec: app_config.ProviderSpec) -> dict[str, str]:
    """Consent status, for `list`."""
    resolved = app_config.for_provider(spec.name)
    return {
        "cloud_consent": app_config.consent.consent_status(resolved.settings, resolved, "cloud"),
        "credential_consent": app_config.consent.consent_status(
            resolved.settings, resolved, "credential"
        ),
    }


def _provider_to_dict(spec: app_config.ProviderSpec) -> dict:
    consent_info = _consent_summary(spec)
    return {
        "name": spec.name,
        "label": spec.label,
        "kind": spec.kind,
        "dialect": spec.dialect,
        "host": spec.default_host,
        "vision_model": spec.default_vision_model,
        "text_model": spec.default_text_model,
        "requires_api_key": spec.requires_api_key,
        "api_key_env": spec.api_key_env,
        "api_key_set": bool(app_config.resolve_api_key(spec)) if spec.api_key_env else None,
        "credential_command": list(spec.credential_command),
        "supports_model_listing": spec.supports_model_listing,
        "source": spec.source,
        "manifest_path": spec.manifest_path,
        "cloud_consent": consent_info["cloud_consent"],
        "credential_consent": consent_info["credential_consent"],
    }


def _print_provider(spec: app_config.ProviderSpec) -> None:
    print(f"{spec.label}  [{spec.name}]")
    for label, value in ai_manifests.describe_provider(spec):
        print(f"  {label + ':':<15}{value}")
    if spec.kind == "cloud":
        info = _consent_summary(spec)
        print(
            f"  {'Consent:':<15}{info['cloud_consent']} (cloud)"
            + (
                f", credential command {info['credential_consent']}"
                if spec.credential_command
                else ""
            )
        )
    else:
        print(f"  {'Consent:':<15}not-needed (runs on this computer)")


@click.group(
    "providers",
    cls=Group,
    context_settings=CONTEXT_SETTINGS,
    epilog=EPILOG,
    invoke_without_command=True,
)
@click.pass_context
def command(ctx: click.Context) -> None:
    """Add, inspect, test and remove AI provider manifests."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(list_command)


@command.command("list")
@click.option("--json", "as_json", is_flag=True)
def list_command(*, as_json: bool) -> None:
    """Show every registered provider."""
    providers = app_config.registered_providers()
    problems = app_config.manifest_problems()

    if as_json:
        body = {
            "providers": [_provider_to_dict(spec) for spec in providers],
            "problems": list(problems),
            "examples": list(ai_manifests.example_names()),
        }
        print(json.dumps(body, indent=2))
        return

    for index, spec in enumerate(providers):
        if index:
            print()
        _print_provider(spec)

    if problems:
        print()
        print("Provider manifests skipped (broken or invalid):")
        for problem in problems:
            print(f"  - {problem}")

    examples = ai_manifests.example_names()
    if examples:
        print()
        print("Examples you can start from:")
        for name in examples:
            print(f"  {name:<20} opengrader-providers add --example {name}")


def _install(text: str, *, force: bool) -> tuple[Path, str]:
    try:
        dest, spec = ai_manifests.install_manifest(text, force=force)
    except ai_manifests.ManifestExistsError as exc:
        _error(f"{exc} Pass --force to overwrite it, or pick a different name.")
        sys.exit(1)
    except ai_manifests.ManifestError as exc:
        _error(str(exc))
        sys.exit(1)
    return dest, spec.name


def _confirm_add(dest: Path, name: str) -> None:
    problems = app_config.reload_manifests()
    live = {spec.name for spec in app_config.registered_providers()}
    if name in live:
        spec = app_config.get_provider(name)
        print(f"Wrote {dest}")
        print(f"'{name}' is now registered and available to every tool.")
        if spec.kind == "cloud":
            print(
                f"This is a CLOUD provider: choosing it will send data to "
                f"{spec.default_host}."
            )
        if spec.api_key_env:
            print(
                f"Set {spec.api_key_env} before using it — "
                f"'opengrader-providers test {name}' will tell you if it resolves."
            )
        if spec.credential_command:
            print(
                f"credential_command is set: Opengrader will run it only after you "
                f"approve it with 'opengrader-providers allow-credential-command {name}'."
            )
    else:
        reason = next((p for p in problems if str(dest) in p), None)
        _error(
            f"wrote {dest}, but it did not load back in"
            + (f": {reason}" if reason else ".")
        )
        sys.exit(1)


@command.command("add", context_settings={"allow_extra_args": True})
@click.option(
    "--example",
    default="",
    help="Copy a shipped example verbatim (see 'list' for the names).",
)
@click.option(
    "--from",
    "from_path",
    default="",
    help="Validate and copy in an existing manifest file.",
)
@click.option("--name", default="", help="Provider id (flags mode).")
@click.option("--label", default="", help="What the dropdown shows.")
@click.option(
    "--dialect",
    type=click.Choice((*app_config.dialect_names(), ai_manifests.PYDANTIC_AI_DIALECT)),
)
@click.option(
    "--host",
    default="",
    help=(
        "API base, e.g. https://host/v1. Optional for --dialect pydantic-ai "
        "when the provider prefix (e.g. bedrock:, vertexai:) has no caller-set URL."
    ),
)
@click.option("--vision-model", default="")
@click.option("--text-model", default="")
@click.option(
    "--api-key-env",
    default="",
    help="Name of the environment variable holding the key (never the key).",
)
@click.option("--setup-hint", default="")
@click.option("--summary", default="")
@click.option(
    "--supports-model-listing",
    is_flag=True,
    help="Set if this service can enumerate the models a key may use.",
)
@click.option(
    "--credential-command",
    "credential_program",
    metavar="COMMAND [ARG]...",
    help=(
        "Command whose stdout would be the key. Stored, but never run until "
        "'opengrader-providers allow-credential-command NAME' approves it."
    ),
)
@click.option("--input-per-mtok", type=float)
@click.option("--output-per-mtok", type=float)
@click.option("--force", is_flag=True, help="Overwrite an existing manifest.")
@click.pass_context
def add_command(
    ctx: click.Context,
    *,
    example: str,
    from_path: str,
    name: str,
    label: str,
    dialect: str | None,
    host: str,
    vision_model: str,
    text_model: str,
    api_key_env: str,
    setup_hint: str,
    summary: str,
    supports_model_listing: bool,
    credential_program: str | None,
    input_per_mtok: float | None,
    output_per_mtok: float | None,
    force: bool,
) -> None:
    """Add a provider from a shipped example, a file, or flags."""
    if ctx.args and credential_program is None:
        raise click.UsageError(f"Got unexpected extra arguments ({' '.join(ctx.args)})")
    credential_command = (
        (credential_program, *ctx.args) if credential_program is not None else ()
    )

    modes = [bool(example), bool(from_path), bool(name)]
    if sum(modes) != 1:
        _error(
            "pass exactly one of: --example NAME, --from PATH, or --name (with "
            "--label/--dialect/--host/--vision-model/--text-model)."
        )
        sys.exit(1)

    if example:
        try:
            text = ai_manifests.example_text(example)
        except ai_manifests.ManifestError as exc:
            _error(str(exc))
            sys.exit(1)
        _confirm_add(*_install(text, force=force))
        return

    if from_path:
        source = Path(from_path).expanduser()
        if not source.is_file():
            _error(f"no such file: {source}")
            sys.exit(1)
        try:
            ai_manifests.load_file(source)
            text = source.read_text(encoding="utf-8")
        except (ai_manifests.ManifestError, OSError, UnicodeDecodeError) as exc:
            _error(str(exc))
            sys.exit(1)
        _confirm_add(*_install(text, force=force))
        return

    missing = [
        flag
        for flag, value in (
            ("--label", label),
            ("--dialect", dialect),
            ("--vision-model", vision_model),
            ("--text-model", text_model),
        )
        if not value
    ]
    # host is the one required field the pydantic-ai backend does not need:
    # a prefix with no caller-supplied endpoint (Bedrock, Vertex, ...) uses
    # the SDK's own — see manifests.parse_manifest / _parse_pydantic_ai_manifest.
    if not host and dialect != ai_manifests.PYDANTIC_AI_DIALECT:
        missing.append("--host")
    if missing:
        _error(f"--name also needs: {', '.join(missing)}.")
        sys.exit(1)

    pricing = None
    if input_per_mtok is not None or output_per_mtok is not None:
        pricing = app_config.ProviderPricing(
            input_per_mtok=input_per_mtok or 0.0,
            output_per_mtok=output_per_mtok or 0.0,
        )

    try:
        text = ai_manifests.render_manifest(
            name=name,
            label=label,
            dialect=dialect,
            host=host,
            vision_model=vision_model,
            text_model=text_model,
            api_key_env=api_key_env or "",
            setup_hint=setup_hint or "",
            summary=summary or "",
            supports_model_listing=supports_model_listing,
            credential_command=credential_command,
            pricing=pricing,
        )
    except ai_manifests.ManifestError as exc:
        _error(str(exc))
        sys.exit(1)

    _confirm_add(*_install(text, force=force))


def _print_valid(path: Path, spec: app_config.ProviderSpec) -> None:
    print(f"OK    {path}")
    print(f"      name: {spec.name}  kind: {spec.kind} (derived from host {spec.default_host})")


def _print_invalid(path: Path, exc: Exception) -> None:
    print(f"FAIL  {path}")
    print(f"      {exc}")


def _validate_one(path: Path) -> bool:
    try:
        spec = ai_manifests.load_file(path)
    except ai_manifests.ManifestError as exc:
        _print_invalid(path, exc)
        return False
    _print_valid(path, spec)
    return True


@command.command("prompt")
def prompt_command() -> None:
    """Print a prompt for an AI assistant that writes a provider file for you.

    Paste the output into ChatGPT, Claude, Gemini or any other assistant,
    answer its questions, and save the file it gives you with
    `opengrader providers add --from FILE`. The setup screen's "My service is not
    in this list" does the same with a Copy button and a box to paste into.
    """
    from opengrader.core.app_config import setup as ai_setup

    click.echo(ai_setup.assistant_prompt(), nl=False)


@command.command("validate")
@click.argument("target", required=False, default="")
def validate_command(*, target: str) -> None:
    """Validate one manifest, one provider, or all of them.

    TARGET is a file path or a provider name.
    """
    if not target:
        directory = app_config.manifests_dir()
        try:
            files = sorted(
                (p for p in directory.iterdir() if p.is_file() and p.suffix == ".toml"),
                key=lambda p: p.name,
            )
        except OSError:
            files = []
        if not files:
            print(f"No manifests found in {directory}.")
            return
        ok = True
        for index, path in enumerate(files):
            if index:
                print()
            ok = _validate_one(path) and ok
        if not ok:
            sys.exit(2)
        return

    path_candidate = Path(target).expanduser()
    if path_candidate.is_file():
        if not _validate_one(path_candidate):
            sys.exit(2)
        return

    file_candidate = app_config.manifests_dir() / f"{target}.toml"
    if file_candidate.is_file():
        if not _validate_one(file_candidate):
            sys.exit(2)
        return

    known = next(
        (spec for spec in app_config.registered_providers() if spec.name == target), None
    )
    if known is not None and known.source == "builtin":
        _error(f"'{target}' is a built-in provider — there is no manifest file for it.")
        sys.exit(1)

    _error(
        f"no manifest found for {target!r}. Looked for {path_candidate} and "
        f"{file_candidate}."
    )
    sys.exit(1)


@command.command("test")
@click.argument("name", metavar="PROVIDER")
@click.option(
    "--yes",
    is_flag=True,
    help="Skip the cloud-provider confirmation (for scripts).",
)
def connection_test_command(*, name: str, yes: bool) -> None:
    """Make one real, tiny call to a provider and report latency.

    PROVIDER is a registered provider's name.
    """
    known = {spec.name for spec in app_config.registered_providers()}
    if name not in known:
        _error(
            f"no provider named {name!r}. Known providers: "
            f"{', '.join(sorted(known))}."
        )
        sys.exit(2)

    resolved = app_config.for_provider(name)
    print(
        f"Testing {resolved.provider.label} ({resolved.kind}) at {resolved.host} ..."
    )

    if resolved.kind == "cloud":
        if not yes:
            if sys.stdin.isatty():
                print(
                    f"This sends a short test prompt (no student data) to "
                    f"{resolved.host}, a cloud service."
                )
                try:
                    answer = input("Continue? [y/N]: ").strip().lower()
                except EOFError:
                    answer = ""
                if answer not in ("y", "yes"):
                    _error("not confirmed; re-run with --yes to proceed.")
                    sys.exit(2)
            else:
                _error(
                    f"{resolved.provider.label} is a cloud provider "
                    f"({resolved.host}). Re-run with --yes to confirm sending a "
                    "test prompt to it."
                )
                sys.exit(2)

    try:
        app_config.ensure_ready(resolved, purpose="text")
    except app_config.AIUnavailableError as exc:
        _error(str(exc))
        sys.exit(2)

    try:
        generate = app_config.text_generate(resolved)
        start = time.perf_counter()
        reply = generate(_TEST_PROMPT)
        elapsed = time.perf_counter() - start
    except Exception as exc:  # noqa: BLE001 - a wire failure is not a crash here
        _error(str(exc))
        if resolved.provider.setup_hint:
            print(f"       {resolved.provider.setup_hint}", file=sys.stderr)
        sys.exit(2)

    print(f"Reply: {reply.strip()!r}  ({elapsed:.2f}s)")
    print(f"{resolved.provider.label} is reachable and working.")


def _known_provider(name: str) -> app_config.ProviderSpec:
    spec = next((s for s in app_config.registered_providers() if s.name == name), None)
    if spec is None:
        known = ", ".join(sorted(s.name for s in app_config.registered_providers()))
        _error(f"no provider named {name!r}. Known providers: {known}.")
        sys.exit(2)
    return spec


def _require_tty(purpose: str) -> None:
    if not sys.stdin.isatty():
        _error(
            f"{purpose} needs an interactive terminal to type a confirmation; "
            "refusing rather than guessing. Run this command directly in a terminal."
        )
        sys.exit(2)


def _typed_yes(prompt: str) -> bool:
    try:
        answer = input(prompt).strip().lower()
    except EOFError:
        answer = ""
    return answer == "yes"


@command.command("consent")
@click.argument("provider")
def consent_command(*, provider: str) -> None:
    """Grant cloud consent: read what leaves, then type yes.

    PROVIDER is a registered cloud provider's name.
    """
    _known_provider(provider)
    resolved = app_config.for_provider(provider)
    if resolved.kind == "local":
        _error(
            f"{resolved.provider.label} runs on this computer; there is nothing to "
            "consent to."
        )
        sys.exit(2)
    _require_tty("granting cloud consent")
    print(app_config.consent.cloud_consent_text(resolved).as_text())
    if not _typed_yes('Type "yes" to send student work to this service: '):
        _error("not confirmed; nothing was saved.")
        sys.exit(2)
    settings = app_config.load_settings()
    try:
        settings = app_config.consent.grant_cloud_consent(settings, resolved)
    except ValueError as exc:
        _error(str(exc))
        sys.exit(2)
    app_config.save_settings(settings)
    print(
        f"Consent recorded: student work may now be sent to {resolved.host} "
        f"using {resolved.provider.label}."
    )
    print(f"Revoke at any time: opengrader-providers revoke {resolved.provider.name}")


@command.command("revoke")
@click.argument("provider")
def revoke_command(*, provider: str) -> None:
    """Revoke a provider's cloud consent.

    PROVIDER is a provider's name.
    """
    _known_provider(provider)
    settings = app_config.load_settings()
    settings = app_config.consent.revoke_cloud_consent(settings, provider)
    app_config.save_settings(settings)
    print(f"Cloud consent for {provider!r} revoked.")


@command.command("allow-credential-command")
@click.argument("provider")
def allow_credential_command_command(*, provider: str) -> None:
    """Approve running a manifest's credential_command.

    PROVIDER is a registered provider's name.
    """
    spec = _known_provider(provider)
    if not spec.credential_command:
        _error(f"{spec.label} names no credential_command.")
        sys.exit(2)
    _require_tty("approving a credential command")
    print(app_config.consent.credential_consent_text(spec).as_text())
    if not _typed_yes('Type "yes" to allow this exact command to run: '):
        _error("not confirmed; nothing was saved.")
        sys.exit(2)
    settings = app_config.load_settings()
    try:
        settings = app_config.consent.grant_credential_consent(settings, spec)
    except ValueError as exc:
        _error(str(exc))
        sys.exit(2)
    app_config.save_settings(settings)
    print(f"Approved: {spec.label}'s credential_command may now run to fetch its key.")
    print(
        "Revoke at any time: "
        f"opengrader-providers revoke-credential-command {spec.name}"
    )


@command.command("revoke-credential-command")
@click.argument("provider")
def revoke_credential_command_command(*, provider: str) -> None:
    """Revoke a provider's credential_command approval.

    PROVIDER is a provider's name.
    """
    _known_provider(provider)
    settings = app_config.load_settings()
    settings = app_config.consent.revoke_credential_consent(settings, provider)
    app_config.save_settings(settings)
    print(f"credential_command approval for {provider!r} revoked.")


@command.command("remove")
@click.argument("name", metavar="PROVIDER")
@click.option("--yes", is_flag=True, help="Skip the confirmation prompt.")
def remove_command(*, name: str, yes: bool) -> None:
    """Delete a user-added manifest.

    PROVIDER is a user-added provider's name.
    """
    spec = next(
        (s for s in app_config.registered_providers() if s.name == name), None
    )
    if spec is None:
        _error(
            f"no provider named {name!r}. Run 'opengrader-providers list' to "
            "see what is registered."
        )
        sys.exit(1)

    if spec.source != "user":
        _error(
            f"{name!r} is a built-in provider and cannot be removed. Only a "
            "provider you added yourself can be."
        )
        sys.exit(1)

    manifest_path = Path(spec.manifest_path)

    if not yes:
        print(f"Would delete {manifest_path} ({spec.label}, {spec.kind}).")
        if sys.stdin.isatty():
            try:
                answer = input("Delete it? [y/N]: ").strip().lower()
            except EOFError:
                answer = ""
        else:
            answer = ""
        if answer not in ("y", "yes"):
            print("Nothing removed. Re-run with --yes to skip this prompt.")
            return

    try:
        ai_manifests.remove_manifest(spec)
    except ai_manifests.ManifestError as exc:
        _error(str(exc))
        sys.exit(1)

    print(f"Removed {name!r} ({manifest_path}).")
    app_config.reload_manifests()

    settings = app_config.load_settings()
    if settings.provider == name:
        print(
            f"'{name}' was your active AI provider. Opengrader will now fall "
            f"back to {app_config.DEFAULT_PROVIDER!r} until you choose another "
            "in AI Setup."
        )


if __name__ == "__main__":  # pragma: no cover
    command()
