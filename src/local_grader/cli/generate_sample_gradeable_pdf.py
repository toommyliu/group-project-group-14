import json
import random
from argparse import ArgumentParser, RawDescriptionHelpFormatter
from pathlib import Path

from local_grader.core.pdf.templates import (
    generate_template_pdf,
    generate_submission_pdf,
    generate_prompts,
    DEFAULT_FONT,
)

SAMPLE_NAMES = [
    "Alice Johnson",
    "Bob Smith",
    "Carol Williams",
    "David Brown",
    "Emma Davis",
    "Frank Miller",
    "Grace Wilson",
    "Henry Moore",
    "Isabel Taylor",
    "Jack Anderson",
]
SAMPLE_IDS = [
    "012345678",
    "123456789",
    "234567890",
    "345678901",
    "456789012",
]


def run_cli() -> None:
    parser = ArgumentParser(
        description="Generate sample gradeable unit template and submission PDFs for testing.",
        epilog=(
            "examples:\n"
            "  poetry run generate_sample_gradeable_pdf --output tests/sample_output --write-spec\n"
            "  poetry run generate_sample_gradeable_pdf --output tests/sample_output --versions A B --write-spec\n"
        ),
        formatter_class=RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--output", required=True, help="Output directory for generated PDFs."
    )
    parser.add_argument(
        "--unit-title",
        default="CMPE 195 Sample Exam",
        help="Title shown on template pages (default: 'CMPE 195 Sample Exam').",
    )
    parser.add_argument(
        "--versions",
        nargs="+",
        default=["A", "B", "C"],
        help="Version IDs to generate.",
    )
    parser.add_argument(
        "--questions", type=int, default=6, help="Number of questions to generate."
    )
    parser.add_argument(
        "--submissions-per-version",
        type=int,
        default=2,
        help="Number of synthetic submissions per version.",
    )
    parser.add_argument(
        "--template-only", action="store_true", help="Only generate templates."
    )
    parser.add_argument(
        "--font",
        default=DEFAULT_FONT,
        help=f"Font to use for text (standard PDF fonts like 'tiro', 'cour', 'helv') (default: '{DEFAULT_FONT}').",
    )
    parser.add_argument(
        "--write-spec", action="store_true", help="Write the generated layout spec to a JSON file."
    )
    args = parser.parse_args()

    random.seed(None)

    output_dir = Path(args.output)
    templates_dir = output_dir / "templates"
    submissions_dir = output_dir / "submissions"
    templates_dir.mkdir(parents=True, exist_ok=True)
    if not args.template_only:
        submissions_dir.mkdir(parents=True, exist_ok=True)

    prompts = generate_prompts(args.questions)
    questions = [(f"q{i + 1}", random.randint(5, 20), prompts[i]) for i in range(args.questions)]

    print(f"Generating templates for versions: {args.versions}")
    generated_specs = {}
    for v in args.versions:
        out = templates_dir / f"template-v{v}.pdf"
        spec = generate_template_pdf(
            out,
            v,
            questions,
            args.unit_title,
            all_versions=args.versions,
            fontname=args.font,
        )
        generated_specs[v] = spec
        print(f"  Wrote {out}")

    if args.write_spec and generated_specs:
        any_spec = next(iter(generated_specs.values()))
        spec_out = output_dir / "template_spec.generated.json"
        with open(spec_out, "w") as f:
            json.dump(any_spec, f, indent=2)
        print(f"  Wrote {spec_out}")

    if not args.template_only:
        print(f"\nGenerating {args.submissions_per_version} submissions per version...")
        names_pool = SAMPLE_NAMES * 10
        ids_pool = SAMPLE_IDS * 10
        for v in args.versions:
            for _ in range(args.submissions_per_version):
                name = random.choice(names_pool)
                sid = random.choice(ids_pool)
                slug = name.lower().replace(" ", "_")
                out = submissions_dir / f"submission-{slug}-{sid}-v{v}.pdf"
                generate_submission_pdf(
                    out,
                    v,
                    name,
                    sid,
                    questions,
                    args.unit_title,
                    fontname=args.font,
                )
                print(f"  Wrote {out}")

    print("\nDone.")

if __name__ == "__main__":
    run_cli()
