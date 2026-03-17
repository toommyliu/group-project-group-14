import os

from pypdf import PdfReader, PdfWriter


def pdf_splitter(path):
    filename = os.path.splitext(os.path.basename(path))[0]

    pdf = PdfReader(path)
    for page in range(len(pdf.pages)):
        pdf_writer = PdfWriter()
        pdf_writer.add_page(pdf.pages[page])

        output_filename = "{}_page_{}.pdf".format(filename, page + 1)

        with open(output_filename, "wb") as out:
            pdf_writer.write(out)

        print("Created: {}".format(output_filename))


if __name__ == "__main__":
    path = input("Enter path to pdf: ")
    path = path.strip()
    if path is None or not len(path):
        raise Exception("got no path!")

    pdf_splitter(path)
