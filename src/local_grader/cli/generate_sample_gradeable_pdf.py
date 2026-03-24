import json
import random
from argparse import ArgumentParser, RawDescriptionHelpFormatter
from pathlib import Path

from local_grader.core.pdf.templates import (
    CMU_BOLD_FILE,
    CMU_REGULAR_FILE,
    generate_prompts,
    generate_submission_pdf,
    generate_template_pdf,
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
            "  uv run sample-pdf --output tests/sample_output --write-spec\n"
            "  uv run sample-pdf --output tests/sample_output --versions A B --write-spec\n"
            '  uv run sample-pdf --output tests/sample_output --instructions "closed book, closed notes"\n'
        ),
        formatter_class=RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--output", required=True, help="Output directory for generated PDFs."
    )
    # TODO: support templates/placeholders to dynamically populate?
    parser.add_argument(
        "--title",
        default="Quiz 1: Shell/Linux Commands\nCMPE195A: Senior Design Project I\nSan Jose State University\nFriday, March 22, 2026",
        help="Title shown on template pages. Put each header line on a new line.",
    )
    parser.add_argument(
        "--instructions",
        default=None,
        help="Optional instructions shown below Student ID (for example: 'closed book, closed notes').",
    )
    parser.add_argument(
        "--continued-page-header",
        default=None,
        help="Optional header text shown on page 2+ (defaults to the first title line).",
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
        "--font-regular-file",
        default=str(CMU_REGULAR_FILE),
        help=f"Path to regular font file (default: {CMU_REGULAR_FILE}).",
    )
    parser.add_argument(
        "--font-bold-file",
        default=str(CMU_BOLD_FILE),
        help=f"Path to bold font file (default: {CMU_BOLD_FILE}).",
    )
    parser.add_argument(
        "--write-spec",
        action="store_true",
        help="Write the generated layout spec to a JSON file.",
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
    questions = [
        (f"q{i + 1}", random.randint(5, 20), prompts[i]) for i in range(args.questions)
    ]

    print(f"Generating templates for versions: {args.versions}")
    generated_specs = {}
    for v in args.versions:
        out = templates_dir / f"template-v{v}.pdf"
        spec = generate_template_pdf(
            out,
            v,
            questions,
            args.title,
            instructions=args.instructions,
            continued_page_header=args.continued_page_header,
            all_versions=args.versions,
            font_regular_file=args.font_regular_file,
            font_bold_file=args.font_bold_file,
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
                    args.title,
                    instructions=args.instructions,
                    continued_page_header=args.continued_page_header,
                    font_regular_file=args.font_regular_file,
                    font_bold_file=args.font_bold_file,
                )
                print(f"  Wrote {out}")

    print("\nDone.")


if __name__ == "__main__":
    run_cli()
