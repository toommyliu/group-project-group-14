#!/usr/bin/env python
"""Phase 0 vision-parity spike — CLI entry point.

See README.md for what this measures and why it is built the way it is
(most importantly: "The ground-truth trap"). Quick orientation:

  - Crop discovery walks samples/**/*.submission.md (crops.py).
  - gold.toml is the ONLY legitimate reference for CER (metrics.py).
  - The primary output is an adjudication table: every backend's
    transcription of every crop, side by side, for a human to read — this
    works on day one with an empty gold.toml.
  - CER is secondary, computed only over crops with a filled-in gold entry,
    and always reported with n.

Safety, not optional (AI_README.md §7.7 L4):
  - A cloud backend (anthropic-messages, openai-chat) requires
    --yes-send-to-cloud. Without it: print what would be sent, exit 2,
    make zero HTTP calls — checked before ANY backend runs, including a
    local one requested in the same invocation.
  - API keys are read from an environment variable NAMED on the command
    line (--api-key-env backend=ENV_VAR_NAME). Never accepted as an
    argument, never written to any output file, and scrubbed from error
    text before it can reach the table, the JSON, or the terminal.

Example invocations:

    # Structural dry run — no network, no key, exercises discovery + gold
    # loading + table rendering.
    uv run python run_spike.py --backends ollama --dry-run

    # Refuses without sending anything (exit code 2):
    uv run python run_spike.py --backends anthropic-messages \\
        --anthropic-model claude-3-5-sonnet-20241022

    # A real cloud run (NOT executed by this spike's author without
    # explicit authorization — see README.md):
    uv run python run_spike.py --backends ollama anthropic-messages \\
        --anthropic-model claude-3-5-sonnet-20241022 \\
        --api-key-env anthropic-messages=ANTHROPIC_API_KEY \\
        --input-per-mtok anthropic-messages=3.0 \\
        --output-per-mtok anthropic-messages=15.0 \\
        --yes-send-to-cloud
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from backends import anthropic_backend, ollama_backend, openai_backend  # noqa: E402
from backends.base import BackendConfig, CallResult  # noqa: E402
from crops import Crop, discover_crops  # noqa: E402
from metrics import (  # noqa: E402
    AdjudicationRow,
    BackendCropResult,
    build_adjudication_rows,
    compute_cer_summary,
    render_cer_summary,
    render_table,
)

SPIKE_DIR = Path(__file__).resolve().parent
REPO_ROOT = SPIKE_DIR.parent.parent
DEFAULT_SAMPLES_DIR = REPO_ROOT / "samples"
DEFAULT_GOLD_PATH = SPIKE_DIR / "gold.toml"

CLOUD_BACKENDS = {"anthropic-messages", "openai-chat"}
ALL_BACKENDS = ["ollama", "anthropic-messages", "openai-chat"]

BACKEND_MODULES = {
    "ollama": ollama_backend,
    "anthropic-messages": anthropic_backend,
    "openai-chat": openai_backend,
}


def _parse_kv_list(items: list[str] | None, flag: str) -> dict[str, str]:
    """Parse repeated `--flag backend=value` arguments into a dict."""
    result: dict[str, str] = {}
    for item in items or []:
        if "=" not in item:
            raise SystemExit(f"error: {flag} expects backend=value, got {item!r}")
        key, _, value = item.partition("=")
        result[key.strip()] = value.strip()
    return result


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Phase 0 spike: measure whether a hosted vision API transcribes "
            "samples/ answer crops at least as well as the current Ollama "
            "path, with real (not estimated) token counts. Not part of the "
            "shipped tool — see README.md."
        ),
    )
    parser.add_argument(
        "--backends",
        nargs="+",
        choices=ALL_BACKENDS,
        default=["ollama"],
        help="Which backend(s) to run. anthropic-messages and openai-chat are cloud backends.",
    )
    parser.add_argument(
        "--samples-dir",
        type=Path,
        default=DEFAULT_SAMPLES_DIR,
        help=f"Root to search for */student-submissions/*/*.submission.md (default: {DEFAULT_SAMPLES_DIR}).",
    )
    parser.add_argument(
        "--gold",
        type=Path,
        default=DEFAULT_GOLD_PATH,
        help=f"Path to the human-verified gold TOML file (default: {DEFAULT_GOLD_PATH}).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only process the first N discovered crops (smoke testing).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Discover crops, load gold, render table structure. Makes ZERO HTTP calls to any backend.",
    )
    parser.add_argument(
        "--yes-send-to-cloud",
        action="store_true",
        help="Required to actually run a cloud backend (anthropic-messages, openai-chat). Local ollama never needs this.",
    )
    parser.add_argument(
        "--api-key-env",
        action="append",
        metavar="BACKEND=ENV_VAR_NAME",
        help=(
            "Name (not value) of the environment variable holding the API key for a "
            "cloud backend, e.g. --api-key-env anthropic-messages=ANTHROPIC_API_KEY. "
            "Repeatable, one per cloud backend. The key itself is never accepted here."
        ),
    )
    parser.add_argument(
        "--project-cohort",
        type=int,
        default=141,
        help="Cohort size used to project total cost from measured per-crop tokens (default: 141, per AI_README.md §7.7 L4).",
    )
    parser.add_argument(
        "--input-per-mtok",
        action="append",
        metavar="BACKEND=RATE",
        help="Price per million input tokens for a backend, e.g. anthropic-messages=3.0. No vendor price is hardcoded.",
    )
    parser.add_argument(
        "--output-per-mtok",
        action="append",
        metavar="BACKEND=RATE",
        help="Price per million output tokens for a backend, e.g. anthropic-messages=15.0.",
    )

    parser.add_argument("--ollama-host", default=None, help="Override the Ollama host (default: from ai_config.resolve()).")
    parser.add_argument("--ollama-model", default=None, help="Override the Ollama vision model (default: from ai_config.resolve()).")
    parser.add_argument("--anthropic-host", default="https://api.anthropic.com", help="Anthropic API host.")
    parser.add_argument("--anthropic-model", default=None, help="Anthropic model id, e.g. claude-3-5-sonnet-20241022.")
    parser.add_argument("--openai-host", default="https://api.openai.com/v1", help="OpenAI-compatible API base (path up to but excluding /chat/completions).")
    parser.add_argument("--openai-model", default=None, help="OpenAI-compatible model id, e.g. gpt-4o.")

    parser.add_argument(
        "--json-out",
        type=Path,
        default=SPIKE_DIR / "spike-results.json",
        help="Where to write the diffable JSON report (default: spike-results.json next to this script).",
    )
    parser.add_argument("--table-width", type=int, default=40, help="Column width for the human-readable table.")
    return parser


def load_gold(path: Path) -> dict[str, dict]:
    if not path.is_file():
        return {}
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    return data.get("crops", {})


def build_backend_config(name: str, args: argparse.Namespace, api_key_envs: dict[str, str]) -> BackendConfig:
    if name == "ollama":
        host = args.ollama_host
        model = args.ollama_model
        if host is None or model is None:
            # Reuse the real resolution path — the point of the spike is to
            # measure what actually ships, not a hand-picked config.
            from opengrader.core import ai_config

            resolved = ai_config.resolve()
            host = host or resolved.host
            model = model or resolved.vision_model
        return BackendConfig(host=host, model=model, api_key=None)

    if name == "anthropic-messages":
        env_name = api_key_envs.get(name)
        api_key = None
        if env_name:
            import os

            api_key = os.environ.get(env_name) or None
        return BackendConfig(host=args.anthropic_host, model=args.anthropic_model or "", api_key=api_key)

    if name == "openai-chat":
        env_name = api_key_envs.get(name)
        api_key = None
        if env_name:
            import os

            api_key = os.environ.get(env_name) or None
        return BackendConfig(host=args.openai_host, model=args.openai_model or "", api_key=api_key)

    raise ValueError(f"unknown backend {name!r}")


def destination_summary(backend_name: str, config: BackendConfig, crop_count: int) -> str:
    return f"{backend_name}: {crop_count} crop(s) -> {config.host}"


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    api_key_envs = _parse_kv_list(args.api_key_env, "--api-key-env")
    input_rates = _parse_kv_list(args.input_per_mtok, "--input-per-mtok")
    output_rates = _parse_kv_list(args.output_per_mtok, "--output-per-mtok")

    crops = discover_crops(args.samples_dir)
    if args.limit is not None:
        crops = crops[: args.limit]
    crops_by_id: dict[str, Crop] = {c.crop_id: c for c in crops}

    gold = load_gold(args.gold)
    gold_filled = sum(1 for entry in gold.values() if str(entry.get("gold_text", "")).strip())

    print(f"Discovered {len(crops)} crop(s) under {args.samples_dir}")
    print(f"Gold file {args.gold}: {len(gold)} stub entr(y/ies), {gold_filled} with a non-empty gold_text")

    backend_configs: dict[str, BackendConfig] = {
        name: build_backend_config(name, args, api_key_envs) for name in args.backends
    }

    # --- Safety gate: cloud consent, checked before ANY backend runs. ---
    cloud_selected = [name for name in args.backends if name in CLOUD_BACKENDS]
    if cloud_selected and not args.yes_send_to_cloud:
        print()
        print("Refusing to run without --yes-send-to-cloud. This is what WOULD be sent:")
        for name in cloud_selected:
            print("  " + destination_summary(name, backend_configs[name], len(crops)))
        print()
        print("No HTTP request has been made. Pass --yes-send-to-cloud to proceed.")
        return 2

    # Visibility, always, before the first call (AI_README.md §7.7 L3/L4).
    print()
    print("Run plan:")
    for name in args.backends:
        print("  " + destination_summary(name, backend_configs[name], len(crops)))
    if args.dry_run:
        print("  (--dry-run: no HTTP calls will be made)")

    for name in cloud_selected:
        cfg = backend_configs[name]
        if not args.dry_run and not cfg.api_key:
            env_name = api_key_envs.get(name, "(not specified via --api-key-env)")
            print(
                f"error: {name} has no API key. Set the environment variable named by "
                f"--api-key-env {name}=<ENV_VAR_NAME> (currently: {env_name})."
            )
            return 2
        if not args.dry_run and not cfg.model:
            print(f"error: {name} has no model configured. Pass --{name.split('-')[0]}-model.")
            return 2

    # --- Capture the shipped prompt once, reused verbatim by every backend. ---
    # This makes NO network call: ollama_vlm.ensure_model_available and
    # ollama_vlm.ollama_transport.generate_raw are swapped for stubs for the
    # duration of the call (see backends/ollama_backend.py). Safe in
    # --dry-run and safe before the cloud-consent gate.
    shared_prompt: str | None = None
    if crops:
        capture_config = backend_configs.get("ollama") or BackendConfig(host="", model="", api_key=None)
        try:
            shared_prompt = ollama_backend.capture_prompt(crops[0].image_path, capture_config)
        except Exception as exc:  # pragma: no cover - defensive; capture makes no network call
            print(f"warning: could not capture the shipped prompt ({exc})")
            shared_prompt = None

    # --- Run each backend over each crop. ---
    results_with_ids: list[tuple[str, str, CallResult]] = []

    for name in args.backends:
        module = BACKEND_MODULES[name]
        config = backend_configs[name]
        for crop in crops:
            if args.dry_run:
                result = CallResult(text=None, input_tokens=None, output_tokens=None, error="dry-run: no call made")
            elif shared_prompt is None:
                result = CallResult(text=None, input_tokens=None, output_tokens=None, error="no prompt captured (no crops?)")
            else:
                result = module.transcribe(crop.image_path, shared_prompt, config)
            results_with_ids.append((name, crop.crop_id, result))

    metric_results = [
        BackendCropResult(
            backend=name,
            crop_id=crop_id,
            text=result.text,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            error=result.error,
        )
        for name, crop_id, result in results_with_ids
    ]

    rows: list[AdjudicationRow] = build_adjudication_rows(crops_by_id, metric_results, gold)

    print()
    print("=== Adjudication table (primary output) ===")
    print(render_table(rows, args.backends, width=args.table_width))

    cer_summaries = compute_cer_summary(rows, args.backends)
    print()
    print("=== CER (secondary; gold-subset only, n always reported) ===")
    print(render_cer_summary(cer_summaries))

    # --- Token + cost report. ---
    print()
    print("=== Token counts (measured, not estimated) and projected cost ===")
    token_report: dict[str, dict] = {}
    for name in args.backends:
        backend_calls = [r for r in metric_results if r.backend == name and r.input_tokens is not None]
        per_crop = [
            {"crop_id": r.crop_id, "input_tokens": r.input_tokens, "output_tokens": r.output_tokens}
            for r in metric_results
            if r.backend == name
        ]
        n = len(backend_calls)
        total_in = sum(r.input_tokens or 0 for r in backend_calls)
        total_out = sum(r.output_tokens or 0 for r in backend_calls)
        avg_in = total_in / n if n else None
        avg_out = total_out / n if n else None

        cost_line = "no pricing supplied"
        projected_cost = None
        rate_in = input_rates.get(name)
        rate_out = output_rates.get(name)
        if avg_in is not None and rate_in is not None and rate_out is not None:
            projected_cost = (
                (avg_in * args.project_cohort) / 1_000_000 * float(rate_in)
                + (avg_out * args.project_cohort) / 1_000_000 * float(rate_out)
            )
            cost_line = f"${projected_cost:.2f} projected for a {args.project_cohort}-crop cohort"

        print(f"{name}: n={n} (of {len(per_crop)} attempted)")
        if n:
            print(f"    avg input_tokens={avg_in:.1f} avg output_tokens={avg_out:.1f}")
        for entry in per_crop:
            print(f"    {entry['crop_id']}: input={entry['input_tokens']} output={entry['output_tokens']}")
        print(f"    cost projection ({args.project_cohort}-crop cohort): {cost_line}")

        token_report[name] = {
            "n_measured": n,
            "n_attempted": len(per_crop),
            "avg_input_tokens": avg_in,
            "avg_output_tokens": avg_out,
            "per_crop": per_crop,
            "project_cohort": args.project_cohort,
            "projected_cost_usd": projected_cost,
        }

    # --- Errors, surfaced plainly (already scrubbed by each backend). ---
    errors = [(name, crop_id, r.error) for name, crop_id, r in results_with_ids if r.error]
    if errors:
        print()
        print("=== Errors ===")
        for name, crop_id, err in errors:
            print(f"{name} {crop_id}: {err}")

    # --- JSON report, diffable across runs. ---
    json_report = {
        "samples_dir": str(args.samples_dir),
        "gold_path": str(args.gold),
        "crop_count": len(crops),
        "gold_entries_total": len(gold),
        "gold_entries_filled": gold_filled,
        "backends": args.backends,
        "dry_run": args.dry_run,
        "adjudication": [
            {
                "crop_id": row.crop_id,
                "incumbent_read_by": row.incumbent_read_by,
                "ollama_incumbent_recorded_text": row.incumbent_text,
                "gold_text": row.gold_text,
                "backend_texts": row.backend_texts,
                "disagreement": row.disagreement,
            }
            for row in rows
        ],
        "cer": [
            {
                "backend": s.backend,
                "n": s.n,
                "mean": s.mean,
                "per_crop": [{"crop_id": cid, "cer": cer} for cid, cer in s.per_crop],
            }
            for s in cer_summaries
        ],
        "tokens_and_cost": token_report,
        "errors": [{"backend": n, "crop_id": c, "error": e} for n, c, e in errors],
    }
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(json_report, indent=2, sort_keys=True), encoding="utf-8")
    print()
    print(f"Wrote JSON results to {args.json_out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
