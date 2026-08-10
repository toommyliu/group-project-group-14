"""Read Opengrader QR codes off a rendered page image (OpenCV).

The counterpart to ``core/qr_codes`` (which *writes* the codes). Reading uses
``cv2.QRCodeDetector`` — already a project dependency for line/box detection — so
recovering a sheet's identity/page carries no new dependency and stays
offline-first. Kept in the ``ocr`` package (the home of the heavy vision stack)
so ``core/qr_codes`` remains a light, cv2-free leaf.

All functions are best-effort: any detector failure, or a page with no readable
Opengrader code, yields an empty result rather than raising — a QR-less assignment
must degrade cleanly to the existing OCR paths.
"""

from __future__ import annotations

import numpy as np
from PIL import Image

from opengrader.core.qr_codes import OpengraderCode, decode_payload


def read_codes_from_image(image: Image.Image) -> list[OpengraderCode]:
    """Detect and decode every Opengrader QR code on one page image.

    Foreign QR codes (anything without our payload prefix) are dropped. Tries the
    multi-code detector first, then a single-code fallback for detectors/scans
    where the multi path finds nothing.
    """
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
    """Return the first Opengrader QR code on the page, or ``None``."""
    codes = read_codes_from_image(image)
    return codes[0] if codes else None
