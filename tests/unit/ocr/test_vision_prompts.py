import base64
import io
from pathlib import Path

from PIL import Image

from opengrader.core.ocr import vision_prompts


def _decoded(encoded: str) -> Image.Image:
    return Image.open(io.BytesIO(base64.b64decode(encoded)))


def test_a_large_crop_is_sent_to_the_model_at_a_readable_but_smaller_size(tmp_path: Path):
    path = tmp_path / "q1.png"
    Image.new("RGB", (2500, 600), "white").save(path)

    sent = _decoded(vision_prompts.image_to_base64(path))

    assert sent.width < 2500 and abs(sent.width / sent.height - 2500 / 600) < 0.01
    assert Image.open(path).size == (2500, 600)


def test_a_small_crop_is_sent_as_it_is(tmp_path: Path):
    path = tmp_path / "id.png"
    Image.new("RGB", (900, 150), "white").save(path)

    assert base64.b64decode(vision_prompts.image_to_base64(path)) == path.read_bytes()
