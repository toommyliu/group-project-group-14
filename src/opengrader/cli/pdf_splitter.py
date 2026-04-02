from pathlib import Path
from argparse import ArgumentParser, Namespace, RawDescriptionHelpFormatter
from opengrader.core.pdf import pdf_splitter

def setup_args() -> Namespace:
    parser = ArgumentParser(
        description="Split a PDF into smaller chunks.",
        epilog=(
            "examples:\n"
            " python -m opengrader.cli.pdf_splitter assets/sample.pdf\n"
            " python -m opengrader.cli.pdf_splitter assets/sample.pdf --chunk 5\n"
        ),
        formatter_class=RawDescriptionHelpFormatter
    )
    parser.add_argument("path", type=str,
                        help="Path to the PDF file.")
    parser.add_argument("--chunk", type=int, default=1, help="Number of pages per chunk (default: 1).")
    return parser.parse_args()


def run_cli():
    args = setup_args()
    final_path = Path(args.path)
    if final_path.suffix.lower() != ".pdf":
        raise ValueError("The provided file must be a PDF.")

    if args.chunk <= 0:
        raise ValueError("Chunk size must be a positive integer.")

    abs_path = final_path.absolute()
    if not abs_path.is_file():
        raise FileNotFoundError(f"File not found: {abs_path}")

    pdf_splitter(abs_path, args.chunk)


if __name__ == "__main__":
    run_cli()
