import os
from pathlib import Path

from pypdf import PdfReader, PdfWriter


def pdf_splitter(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {path.absolute()}")

    # get the filename from the path
    filename = os.path.splitext(os.path.basename(path))[0]
    pdf = PdfReader(path)

    # unlikely?
    if len(pdf.pages) == 0:
        raise ValueError(f"{path.name} has no pages!")

    for page in range(len(pdf.pages)):
        pdf_writer = PdfWriter()
        pdf_writer.add_page(pdf.pages[page])

        output_filename = f"{filename}_page_{page + 1}.pdf"

        with open(output_filename, "wb") as out:
            pdf_writer.write(out)

        print(f"Created: {output_filename}")


if __name__ == "__main__":
    path = input("Enter path to pdf: ")
    path = path.strip()
    if not path or not len(path):
        raise Exception("No path provided")

    final_path = Path(path).absolute()
    pdf_splitter(final_path)
