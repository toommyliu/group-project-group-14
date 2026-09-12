import numpy as np


def apply_grayscale(image: np.ndarray) -> np.ndarray:
    import cv2

    if len(image.shape) == 3 and image.shape[2] == 3:
        return cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    return image
