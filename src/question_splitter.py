import fitz
from pathlib import Path


def split_pdf_by_rectangles(input_pdf: str, output_dir: str):
    doc = fitz.open(input_pdf)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Hardcode question with rectangles: (page_index, x0, y0, x1, y1)
    questions = {
        "question_1_2_3": (0, 100, 180, 550, 275),
        "question_4": (0, 100, 270, 550, 365),
        "question_5": (0, 100, 365, 550, 400),
        "question_6": (0, 100, 400, 550, 440),
        "question_7": (0, 100, 440, 550, 480),
        "question_8": (0, 100, 480, 550, 515),
        "question_9": (0, 100, 515, 550, 580),
        "question_10": (0, 100, 575, 550, 620)
    }

    for name, (page_num, x0, y0, x1, y1) in questions.items():
        page = doc[page_num]
        clip_rect = fitz.Rect(x0, y0, x1, y1)

        new_pdf = fitz.open()
        new_page = new_pdf.new_page(width=clip_rect.width, height=clip_rect.height)

        new_page.show_pdf_page(
            new_page.rect,
            doc,
            page_num,
            clip=clip_rect
        )

        output_path = out_dir / f"{name}.pdf"
        new_pdf.save(output_path)
        new_pdf.close()

        print(f"Saved: {output_path}")

    doc.close()


if __name__ == "__main__":
    split_pdf_by_rectangles("assets/sample2.pdf", "tests/question_split_output")