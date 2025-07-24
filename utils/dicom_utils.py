"""
dicom_utils.py
==============

Low-level helpers for reading DICOM headers quickly, sorting slices,
and discovering series on disk.

Intended to be imported by both extract_dicom_headers.py
and generate_previews.py.
"""
from __future__ import annotations

import os
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterator, List, Tuple

import pydicom
from pydicom.errors import InvalidDicomError

###############################################################################
# Tag helpers
###############################################################################


def hex_to_tag(hex_str: str) -> Tuple[int, int]:
    """Convert '0020000D' → (0x0020, 0x000D)."""
    return int(hex_str[:4], 16), int(hex_str[4:], 16)


###############################################################################
# Header reading
###############################################################################


def read_header(
    dcm_path: Path, fields: List[str] | Tuple[str, ...], *, strict: bool = False
) -> Dict[str, str]:
    """
    Fast header read (skip pixels). Returns {tag_hex: value or ''}.

    If *strict* is True, raises on invalid DICOM; else returns {}.
    """
    try:
        ds = pydicom.dcmread(dcm_path, stop_before_pixels=True, force=True)
    except InvalidDicomError:
        if strict:
            raise
        return {}

    out: Dict[str, str] = {}
    for tag_hex in fields:
        tag = hex_to_tag(tag_hex)
        out[tag_hex] = str(ds.get(tag, ""))
    return out


###############################################################################
# Slice utilities
###############################################################################


def safe_instance_number(ds: pydicom.Dataset, default: int = -1) -> int:
    """Return InstanceNumber or *default* if missing / malformed."""
    try:
        return int(ds.InstanceNumber)
    except Exception:
        return default


def sort_slices_by_instance(file_list: List[Path]) -> List[Path]:
    """Return *file_list* sorted by InstanceNumber (fallback filename)."""
    def key(fp: Path):
        try:
            ds = pydicom.dcmread(fp, stop_before_pixels=True, force=True)
            return safe_instance_number(ds)
        except Exception:
            return -1
    return sorted(file_list, key=key)


def choose_slice_indices(n_slices: int, n_pick: int = 8) -> List[int]:
    """
    Evenly spaced indices from 0..n_slices-1.

    If n_slices < n_pick, returns every available slice index.
    """
    if n_slices <= n_pick:
        return list(range(n_slices))
    return [round(i * (n_slices - 1) / (n_pick - 1)) for i in range(n_pick)]


###############################################################################
# Series discovery (re-used by both main scripts)
###############################################################################


def gather_series_files(root: Path) -> Dict[str, List[Path]]:
    """
    Walk *root* and build {SeriesInstanceUID: [paths sorted by InstanceNumber]}.
    """
    series: Dict[str, List[Path]] = defaultdict(list)
    for dirpath, _dirs, files in os.walk(root):
        for fname in files:
            fpath = Path(dirpath) / fname
            try:
                ds = pydicom.dcmread(fpath, stop_before_pixels=True, force=True)
                series_uid = str(ds.SeriesInstanceUID)
                series[series_uid].append(fpath)
            except Exception:
                # skip non-DICOM or missing SeriesUID
                continue

    # sort each list
    for uid, flist in series.items():
        series[uid] = sort_slices_by_instance(flist)
    return series
