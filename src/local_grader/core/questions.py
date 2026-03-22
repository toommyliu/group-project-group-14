import fitz
from pathlib import Path
from typing import Dict, Tuple

def split_pdf_by_rectangles(input_pdf: str, output_dir: str, questions: Dict[str, Tuple[int, float, float, float, float]]):
    doc = fitz.open(input_pdf)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

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
