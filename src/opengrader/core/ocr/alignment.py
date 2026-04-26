import cv2
import numpy as np
from PIL import Image

from .utils import apply_grayscale


def preprocess_for_ocr(image: Image.Image, deskew: bool = True) -> Image.Image:
    """
    Apply binarization (black-and-white) and deskewing (tries to correct for rotation).
    """
    arr = np.array(image)
    gray = apply_grayscale(arr)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # TODO: suggestion to use Ollama to try and deskew?
    if deskew:
        coords = cv2.findNonZero(binary)
        if coords is not None:
            angle = cv2.minAreaRect(coords)[2]
            if angle < -45:
                angle = 90 + angle
            elif angle > 45:
                angle = angle - 90
            else:
                angle = angle
            if abs(angle) > 0.5:
                M = cv2.getRotationMatrix2D(
                    (gray.shape[1] / 2, gray.shape[0] / 2), angle, 1.0
                )
                binary = cv2.warpAffine(binary, M, (gray.shape[1], gray.shape[0]))
    return Image.fromarray(binary)
