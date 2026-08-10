import numpy as np


def apply_grayscale(image: np.ndarray) -> np.ndarray:
    # Import lazily: cv2 (OpenCV) is a ~30-40ms cold import and this module
    # is reached through core.ocr.layout on the TUI startup chain (setup
    # screen -> submission extractor -> ocr.extractor -> ocr.layout ->
    # ocr.utils), even though grayscale conversion only runs during actual
    # OCR extraction, not at startup.
    import cv2

    if len(image.shape) == 3 and image.shape[2] == 3:
        return cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    return image
