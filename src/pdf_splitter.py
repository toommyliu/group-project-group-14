from pathlib import Path
from pypdf import PdfReader, PdfWriter
from argparse import ArgumentParser, Namespace, RawDescriptionHelpFormatter


def setup_args() -> Namespace:
    parser = ArgumentParser(
        description="Split a PDF into smaller chunks.",
        epilog=(
            "examples:\n"
            " python pdf_splitter.py sample.pdf\n"
            " python pdf_splitter.py sample.pdf --chunk 5\n"
        ),
        formatter_class=RawDescriptionHelpFormatter
    )
    parser.add_argument("path", type=str,
                        help="Path to the PDF file.")
    parser.add_argument("--chunk", type=int, default=1, help="Number of pages per chunk (default: 1).")
    return parser.parse_args()


def format_output_filename(filename: str, start_page: int, end_page: int) -> str:
    if start_page == end_page:
        return f"{filename}-page_{start_page}.pdf"

    return f"{filename}-pages_{start_page}-{end_page}.pdf"


def pdf_splitter(path: Path, pages_per_chunk: int = 1) -> None:
    filename = path.stem  # filename without extension
    pdf = PdfReader(path)

    if len(pdf.pages) == 0:
        raise ValueError(f"{path.name} has no pages!")

    for i in range(0, len(pdf.pages), pages_per_chunk):
        pdf_writer = PdfWriter()
        for page in pdf.pages[i:i + pages_per_chunk]:
            pdf_writer.add_page(page)

        start_page_number = i + 1
        # cap the end page number to the total number of pages in the PDF
        # if the last chunk has fewer pages than pages_per_chunk
        end_page_number = min(i + pages_per_chunk, len(pdf.pages))
        output_filename = format_output_filename(filename, start_page_number, end_page_number)

        with open(output_filename, "wb") as out:
            pdf_writer.write(out)

        print(f"Created: {output_filename}")


if __name__ == "__main__":
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
