"""
image_utils.py
==============

Helpers for converting pixel data to 8-bit thumbnails and saving WebP.
"""
from __future__ import annotations

from pathlib import Path
from typing import Union

import numpy as np
import pydicom
from PIL import Image


def normalize_to_uint8(
    arr: np.ndarray,
    *,
    low_percent: float = 1.0,
    high_percent: float = 99.0,
) -> np.ndarray:
    """
    Clip the array to [low_percent, high_percent] percentiles,
    then scale to 0-255 uint8.  Works for int16 or float input.
    """
    arr = arr.astype(np.float32)

    low, high = np.percentile(arr, [low_percent, high_percent])
    if high <= low:  # avoid divide-by-zero on flat images
        low, high = arr.min(), arr.max() or 1.0
    arr = np.clip(arr, low, high)
    arr = (arr - low) / (high - low) * 255.0
    return arr.astype(np.uint8)


def save_numpy_to_webp(arr8: np.ndarray, out_path: Path):
    """
    Save a 2-D uint8 numpy array as grayscale WebP.
    """
    assert arr8.ndim == 2 and arr8.dtype == np.uint8, "Input must be 2-D uint8"
    Image.fromarray(arr8, mode="L").save(out_path, format="WEBP", quality=85)


def save_dataset_slice(
    ds: Union[pydicom.Dataset, str, Path],
    out_path: Path,
    *,
    low_percent: float = 1.0,
    high_percent: float = 99.0,
):
    """
    Convenience: load *ds* (if str/Path), normalize pixel_array,
    and save WebP thumbnail.
    """
    if not isinstance(ds, pydicom.dataset.Dataset):
        ds = pydicom.dcmread(str(ds), force=True)

    arr = normalize_to_uint8(
        ds.pixel_array, low_percent=low_percent, high_percent=high_percent
    )
    save_numpy_to_webp(arr, out_path)
