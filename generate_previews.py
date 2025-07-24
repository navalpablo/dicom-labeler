#!/usr/bin/env python3
"""
generate_previews.py
--------------------

Step 2 of the DICOM-labeling pipeline.
Create 8 WebP thumbnails per series so the web UI can scroll through them.

Usage
-----
    python generate_previews.py \
        --dicom /path/to/dicom_root \
        --manifest series_info.tsv \
        --outdir previews

    # Force regeneration even if previews already exist
    python generate_previews.py ... --overwrite
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Dict, List

import numpy as np
import pydicom
from PIL import Image
from pydicom.errors import InvalidDicomError
from tqdm import tqdm

###############################################################################
# Helpers
###############################################################################


def safe_instance_number(ds: pydicom.Dataset, default: int) -> int:
    """Return InstanceNumber if present, else *default*."""
    try:
        return int(ds.InstanceNumber)
    except Exception:
        return default


def normalize_to_uint8(arr: np.ndarray) -> np.ndarray:
    """
    Robust 1–99 percentile window, then scale to 0-255 uint8.
    Works for int16 or float pixel data.
    """
    arr = arr.astype(np.float32)
    low, high = np.percentile(arr, [1, 99])
    if high <= low:  # fallback if image is flat
        low, high = arr.min(), arr.max() or 1.0
    arr = np.clip(arr, low, high)
    arr = (arr - low) / (high - low) * 255.0
    return arr.astype(np.uint8)


def save_slice_webp(ds: pydicom.Dataset, out_path: Path):
    """Convert pydicom dataset *ds* to 8-bit grayscale WebP at *out_path*."""
    arr = normalize_to_uint8(ds.pixel_array)
    # PIL expects (H, W) or (H, W, 3); single channel → mode 'L'
    Image.fromarray(arr, mode="L").save(out_path, format="WEBP", quality=85)


def gather_series_files(dicom_root: Path) -> Dict[str, List[Path]]:
    """
    Walk *dicom_root* and build {SeriesInstanceUID: [file paths]}.
    """
    series_files: Dict[str, List[Path]] = {}
    for dirpath, _dirs, files in os.walk(dicom_root):
        for fname in files:
            fpath = Path(dirpath) / fname
            try:
                ds = pydicom.dcmread(fpath, stop_before_pixels=True, force=True)
                series_uid = str(ds.SeriesInstanceUID)
                series_files.setdefault(series_uid, []).append(fpath)
            except (InvalidDicomError, AttributeError, KeyError):
                # Skip non-DICOM or DICOMs without SeriesInstanceUID
                continue
    # Sort paths for each series by InstanceNumber (fallback to filename)
    for uid, flist in series_files.items():
        def sort_key(fp: Path):
            try:
                ds = pydicom.dcmread(fp, stop_before_pixels=True, force=True)
                return safe_instance_number(ds, default=-1)
            except Exception:
                return -1
        flist.sort(key=sort_key)
    return series_files


def choose_slice_indices(n_slices: int, n_pick: int = 8) -> List[int]:
    """
    Return *n_pick* indices evenly spaced from 0..n_slices-1 (inclusive).
    If n_slices < n_pick, return every slice index (no duplication).
    """
    if n_slices <= n_pick:
        return list(range(n_slices))
    return [round(i * (n_slices - 1) / (n_pick - 1)) for i in range(n_pick)]


###############################################################################
# Main
###############################################################################


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate 8 WebP previews per SeriesInstanceUID",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--dicom",
        required=True,
        type=Path,
        help="Root directory containing DICOM studies",
    )
    parser.add_argument(
        "--manifest",
        default=Path("series_info.tsv"),
        type=Path,
        help="TSV created by extract_dicom_headers.py (used to know which SeriesUIDs exist)",
    )
    parser.add_argument(
        "--outdir",
        default=Path("previews"),
        type=Path,
        help="Where to write <SeriesUID>_slice*.webp thumbnails",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Regenerate previews even if they already exist",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    if not args.dicom.exists():
        sys.exit(f"[ERROR] DICOM root {args.dicom} does not exist")

    # 1. Build mapping SeriesUID → list[file]
    print("🔍  Scanning DICOM tree …")
    series_to_files = gather_series_files(args.dicom)
    print(f"   Found {len(series_to_files):,} unique SeriesInstanceUIDs")

    # 2. Determine which UIDs we actually need from manifest (keeps pipeline tidy)
    needed_uids: set[str] = set()
    if args.manifest.exists():
        with args.manifest.open() as f:
            # Header row uses readable names; Series Instance UID col is exactly that string
            header = f.readline().rstrip("\n").split("\t")
            try:
                sid_col = header.index("Series Instance UID")
            except ValueError:
                sid_col = header.index("0020000E")  # fallback if using hex
            for line in f:
                uid = line.rstrip("\n").split("\t")[sid_col]
                if uid:
                    needed_uids.add(uid)
    else:
        needed_uids = set(series_to_files.keys())  # label-less pilot

    # Debug: warn if manifest expects a UID we didn't find
    missing = needed_uids - set(series_to_files.keys())
    if missing:
        print(f"[WARN] {len(missing)} SeriesUIDs listed in manifest but not on disk")

    # 3. Create output folder
    args.outdir.mkdir(parents=True, exist_ok=True)

    # 4. Generate previews
    todo_uids = needed_uids & set(series_to_files.keys())
    for uid in tqdm(sorted(todo_uids), desc="Generating previews"):
        slice_paths = series_to_files[uid]
        if not slice_paths:
            continue
        # Already done?
        slice0_path = args.outdir / f"{uid}_slice0.webp"
        if slice0_path.exists() and not args.overwrite:
            continue

        chosen_idx = choose_slice_indices(len(slice_paths), 8)
        for i, idx in enumerate(chosen_idx):
            src = slice_paths[idx]
            dst = args.outdir / f"{uid}_slice{i}.webp"
            try:
                ds = pydicom.dcmread(src, force=True)
                save_slice_webp(ds, dst)
            except Exception as exc:
                print(f"[WARN]  Failed {uid} slice {i} ({src.name}): {exc}")

        # Optionally write sidecar JSON
        meta = {
            "uid": uid,
            "total_slices": len(slice_paths),
            "selected_indices": chosen_idx,
            "source_paths": [str(slice_paths[i]) for i in chosen_idx],
        }
        with (args.outdir / f"{uid}.json").open("w") as f:
            json.dump(meta, f)

    print(f"\n✅  Previews stored in {args.outdir.resolve()}")


if __name__ == "__main__":
    main()
