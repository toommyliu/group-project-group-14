"""Register, inspect and test the user's own AI provider manifests (§7.5).

Ollama stays the offline default (L0, `About/AI_README.md` §7.7): a fresh
install registers nothing here. Every subcommand that touches a *cloud*
provider says so in the output, because §7.7's L3 asks that a cloud
provider be stated wherever the AI is mentioned, and this command is one of
those places.
"""

from __future__ import annotations

import json
import sys
import time
from argparse import ArgumentParser, Namespace, RawDescriptionHelpFormatter
from pathlib import Path

from opengrader.core import ai_config
from opengrader.core.ai_config import manifests as ai_manifests

EPILOG = """
examples:
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


# --- shared rendering helpers -----------------------------------------------


def _provider_to_dict(spec: ai_config.ProviderSpec) -> dict:
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
        "api_key_set": bool(ai_config.resolve_api_key(spec)) if spec.api_key_env else None,
        "credential_command": list(spec.credential_command),
        "supports_model_listing": spec.supports_model_listing,
        "source": spec.source,
        "manifest_path": spec.manifest_path,
    }


def _print_provider(spec: ai_config.ProviderSpec) -> None:
    print(f"{spec.label}  [{spec.name}]")
    for label, value in ai_manifests.describe_provider(spec):
        print(f"  {label + ':':<15}{value}")


def _cmd_list(args: Namespace) -> None:
    providers = ai_config.registered_providers()
    problems = ai_config.manifest_problems()

    if args.as_json:
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


# --- add ---------------------------------------------------------------


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
    """Reload the registry and say plainly whether the new provider is live.

    A user who has just added a manifest should not have to restart to learn
    whether it took (§7.9 Phase 4's exit criterion) — so this reads
    `reload_manifests()`'s own problem list rather than assuming success.
    """
    problems = ai_config.reload_manifests()
    live = {spec.name for spec in ai_config.registered_providers()}
    if name in live:
        spec = ai_config.get_provider(name)
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
                "credential_command is set but Opengrader will not run it "
                "automatically yet — that needs a consent step that does not "
                "exist yet."
            )
    else:
        # It parsed fine when we validated it, but load_all() re-reads the
        # directory and something about that pass disagreed — say so rather
        # than a silent "succeeded".
        reason = next((p for p in problems if str(dest) in p), None)
        _error(
            f"wrote {dest}, but it did not load back in"
            + (f": {reason}" if reason else ".")
        )
        sys.exit(1)


def _cmd_add(args: Namespace) -> None:
    modes = [bool(args.example), bool(args.from_path), bool(args.name)]
    if sum(modes) != 1:
        _error(
            "pass exactly one of: --example NAME, --from PATH, or --name (with "
            "--label/--dialect/--host/--vision-model/--text-model)."
        )
        sys.exit(1)

    if args.example:
        try:
            text = ai_manifests.example_text(args.example)
        except ai_manifests.ManifestError as exc:
            _error(str(exc))
            sys.exit(1)
        _confirm_add(*_install(text, force=args.force))
        return

    if args.from_path:
        source = Path(args.from_path).expanduser()
        if not source.is_file():
            _error(f"no such file: {source}")
            sys.exit(1)
        try:
            # Validated against its own path first, so an error names the
            # file the user pointed at rather than the one we would write.
            ai_manifests.load_file(source)
            text = source.read_text(encoding="utf-8")
        except (ai_manifests.ManifestError, OSError, UnicodeDecodeError) as exc:
            _error(str(exc))
            sys.exit(1)
        _confirm_add(*_install(text, force=args.force))
        return

    # Flags mode.
    missing = [
        flag
        for flag, value in (
            ("--label", args.label),
            ("--dialect", args.dialect),
            ("--host", args.host),
            ("--vision-model", args.vision_model),
            ("--text-model", args.text_model),
        )
        if not value
    ]
    if missing:
        _error(f"--name also needs: {', '.join(missing)}.")
        sys.exit(1)

    pricing = None
    if args.input_per_mtok is not None or args.output_per_mtok is not None:
        pricing = ai_config.ProviderPricing(
            input_per_mtok=args.input_per_mtok or 0.0,
            output_per_mtok=args.output_per_mtok or 0.0,
        )

    try:
        text = ai_manifests.render_manifest(
            name=args.name,
            label=args.label,
            dialect=args.dialect,
            host=args.host,
            vision_model=args.vision_model,
            text_model=args.text_model,
            api_key_env=args.api_key_env or "",
            setup_hint=args.setup_hint or "",
            summary=args.summary or "",
            supports_model_listing=args.supports_model_listing,
            credential_command=tuple(args.credential_command or ()),
            pricing=pricing,
        )
    except ai_manifests.ManifestError as exc:
        _error(str(exc))
        sys.exit(1)

    _confirm_add(*_install(text, force=args.force))


# --- validate ------------------------------------------------------------


def _print_valid(path: Path, spec: ai_config.ProviderSpec) -> None:
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


def _cmd_validate(args: Namespace) -> None:
    target = args.target

    if not target:
        directory = ai_config.manifests_dir()
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

    file_candidate = ai_config.manifests_dir() / f"{target}.toml"
    if file_candidate.is_file():
        if not _validate_one(file_candidate):
            sys.exit(2)
        return

    known = next(
        (spec for spec in ai_config.registered_providers() if spec.name == target), None
    )
    if known is not None and known.source == "builtin":
        _error(f"'{target}' is a built-in provider — there is no manifest file for it.")
        sys.exit(1)

    _error(
        f"no manifest found for {target!r}. Looked for {path_candidate} and "
        f"{file_candidate}."
    )
    sys.exit(1)


# --- test ------------------------------------------------------------------


def _cmd_test(args: Namespace) -> None:
    name = args.provider
    known = {spec.name for spec in ai_config.registered_providers()}
    if name not in known:
        _error(
            f"no provider named {name!r}. Known providers: "
            f"{', '.join(sorted(known))}."
        )
        sys.exit(2)

    resolved = ai_config.for_provider(name)
    print(
        f"Testing {resolved.provider.label} ({resolved.kind}) at {resolved.host} ..."
    )

    if resolved.kind == "cloud":
        if not args.yes:
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
        ai_config.ensure_ready(resolved, purpose="text")
    except ai_config.AIUnavailableError as exc:
        _error(str(exc))
        sys.exit(2)

    try:
        generate = ai_config.text_generate(resolved)
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


# --- remove ------------------------------------------------------------


def _cmd_remove(args: Namespace) -> None:
    name = args.provider
    spec = next(
        (s for s in ai_config.registered_providers() if s.name == name), None
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

    if not args.yes:
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
    ai_config.reload_manifests()

    settings = ai_config.load_settings()
    if settings.provider == name:
        print(
            f"'{name}' was your active AI provider. Opengrader will now fall "
            f"back to {ai_config.DEFAULT_PROVIDER!r} until you choose another "
            "in AI Setup."
        )


# --- argument parsing --------------------------------------------------


def _add_add_parser(subparsers) -> None:
    add = subparsers.add_parser(
        "add",
        help="Add a provider from a shipped example, a file, or flags.",
    )
    add.add_argument(
        "--example",
        default="",
        help="Copy a shipped example verbatim (see 'list' for the names).",
    )
    add.add_argument(
        "--from",
        dest="from_path",
        default="",
        help="Validate and copy in an existing manifest file.",
    )
    add.add_argument("--name", default="", help="Provider id (flags mode).")
    add.add_argument("--label", default="", help="What the dropdown shows.")
    add.add_argument("--dialect", default="", choices=(*ai_config.dialect_names(), ""))
    add.add_argument("--host", default="", help="API base, e.g. https://host/v1")
    add.add_argument("--vision-model", default="")
    add.add_argument("--text-model", default="")
    add.add_argument(
        "--api-key-env",
        default="",
        help="Name of the environment variable holding the key (never the key).",
    )
    add.add_argument("--setup-hint", default="")
    add.add_argument("--summary", default="")
    add.add_argument(
        "--supports-model-listing",
        action="store_true",
        help="Set if this service can enumerate the models a key may use.",
    )
    add.add_argument(
        "--credential-command",
        nargs="+",
        default=(),
        help=(
            "argv of a command whose stdout is the key (git-credential style). "
            "Never executed by this tool — see L6 in About/AI_README.md."
        ),
    )
    add.add_argument("--input-per-mtok", type=float, default=None)
    add.add_argument("--output-per-mtok", type=float, default=None)
    add.add_argument(
        "--force", action="store_true", help="Overwrite an existing manifest."
    )


def _build_parser() -> ArgumentParser:
    parser = ArgumentParser(
        description=(
            "Add, inspect, test and remove your own AI provider manifests — "
            "the bring-your-own-service surface (About/AI_README.md §7.5)."
        ),
        epilog=EPILOG,
        formatter_class=RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command")

    listing = subparsers.add_parser("list", help="Show every registered provider.")
    listing.add_argument("--json", action="store_true", dest="as_json")

    _add_add_parser(subparsers)

    validate = subparsers.add_parser(
        "validate", help="Validate one manifest, one provider, or all of them."
    )
    validate.add_argument(
        "target", nargs="?", default="", help="A file path or a provider name."
    )

    test = subparsers.add_parser(
        "test", help="Make one real, tiny call to a provider and report latency."
    )
    test.add_argument("provider", help="A registered provider's name.")
    test.add_argument(
        "--yes",
        action="store_true",
        help="Skip the cloud-provider confirmation (for scripts).",
    )

    remove = subparsers.add_parser("remove", help="Delete a user-added manifest.")
    remove.add_argument("provider", help="A user-added provider's name.")
    remove.add_argument(
        "--yes", action="store_true", help="Skip the confirmation prompt."
    )

    return parser


_COMMANDS = {
    "list": _cmd_list,
    "add": _cmd_add,
    "validate": _cmd_validate,
    "test": _cmd_test,
    "remove": _cmd_remove,
}


def run_cli(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    command = args.command or "list"
    if command == "list" and not hasattr(args, "as_json"):
        args.as_json = False
    _COMMANDS[command](args)


def main() -> None:
    run_cli()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
