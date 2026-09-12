from __future__ import annotations

import numpy as np
from PIL import Image

from opengrader.core.qr_codes import OpengraderCode, decode_payload


def read_codes_from_image(image: Image.Image) -> list[OpengraderCode]:
    import cv2

    gray = np.array(image.convert("L"))
    detector = cv2.QRCodeDetector()

    texts: list[str] = []
    try:
        ok, decoded, _points, _straight = detector.detectAndDecodeMulti(gray)
        if ok and decoded:
            texts.extend(t for t in decoded if t)
    except cv2.error:
        pass

    if not texts:
        try:
            text, _points, _straight = detector.detectAndDecode(gray)
            if text:
                texts.append(text)
        except cv2.error:
            pass

    codes: list[OpengraderCode] = []
    for text in texts:
        code = decode_payload(text)
        if code is not None:
            codes.append(code)
    return codes


def read_code_from_image(image: Image.Image) -> OpengraderCode | None:
    codes = read_codes_from_image(image)
    return codes[0] if codes else None
