from pathlib import Path
from pypdf import PdfReader, PdfWriter


def format_output_filename(filename: str, start_page: int, end_page: int) -> str:
    if start_page == end_page:
        return f"{filename}-page_{start_page}.pdf"

    return f"{filename}-pages_{start_page}-{end_page}.pdf"


def pdf_splitter(path: Path, pages_per_chunk: int = 1, output_dir: Path | None = None) -> None:
    filename = path.stem  # filename without extension
    pdf = PdfReader(path)

    if len(pdf.pages) == 0:
        raise ValueError(f"{path.name} has no pages!")

    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)

    for i in range(0, len(pdf.pages), pages_per_chunk):
        pdf_writer = PdfWriter()
        for page in pdf.pages[i : i + pages_per_chunk]:
            pdf_writer.add_page(page)

        start_page_number = i + 1
        # cap the end page number to the total number of pages in the PDF
        # if the last chunk has fewer pages than pages_per_chunk
        end_page_number = min(i + pages_per_chunk, len(pdf.pages))
        output_filename = format_output_filename(
            filename, start_page_number, end_page_number
        )

        output_path = output_dir / output_filename if output_dir else Path(output_filename)
        with open(output_path, "wb") as out:
            pdf_writer.write(out)

        print(f"Created: {output_path}")
