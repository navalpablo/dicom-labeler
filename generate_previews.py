#!/usr/bin/env python3
"""
generate_previews.py – final, fault‑tolerant
============================================
• Reads ./series_info.tsv
• Writes WebPs to ./previews/
• Parallel per‑series, skips non‑image files, RGB→gray, etc.
"""
from __future__ import annotations

import argparse, json, multiprocessing, sys, traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np, pydicom
from PIL import Image
from pydicom import dcmread
from pydicom.errors import InvalidDicomError
from tqdm import tqdm

# -------- fixed locations (repo root) -----------------------------------
SCRIPT_DIR   = Path(__file__).resolve().parent
MANIFEST_TSV = SCRIPT_DIR / "series_info.tsv"
PREVIEWS_DIR = SCRIPT_DIR / "previews"
EXAMPLE_COL  = "Example File"
# ------------------------------------------------------------------------


# --------------------------- helpers ------------------------------------
def normalize_uint8(arr: np.ndarray) -> np.ndarray:
    lo, hi = np.percentile(arr.astype(np.float32), (1, 99))
    if hi <= lo:
        lo, hi = arr.min(), arr.max() or 1
    return ((np.clip(arr, lo, hi) - lo) / (hi - lo) * 255).astype(np.uint8)


def to_grayscale(arr: np.ndarray) -> np.ndarray:
    if arr.ndim == 2:
        return arr
    if arr.ndim == 3:
        return arr.mean(axis=-1)
    raise ValueError("Unsupported pixel array shape")


def save_slice(ds: pydicom.Dataset, dst: Path):
    Image.fromarray(normalize_uint8(to_grayscale(ds.pixel_array)), mode="L") \
         .save(dst, format="WEBP", quality=85)


def choose_indices(n: int, k: int = 8) -> List[int]:
    return list(range(n)) if n <= k else [round(i * (n - 1) / (k - 1)) for i in range(k)]


def load_manifest() -> Dict[str, Path]:
    if not MANIFEST_TSV.exists():
        sys.exit(f"[ERROR] Manifest {MANIFEST_TSV} not found")
    uid_map: Dict[str, Path] = {}
    with MANIFEST_TSV.open() as f:
        header = f.readline().rstrip("\n").split("\t")
        uid_idx, ex_idx = header.index("Series Instance UID"), header.index(EXAMPLE_COL)
        for line in f:
            cols = line.rstrip("\n").split("\t")
            if len(cols) > max(uid_idx, ex_idx):
                uid, ex = cols[uid_idx], cols[ex_idx]
                if uid and ex:
                    uid_map[uid] = Path(ex)
    return uid_map
# ------------------------------------------------------------------------


# --------------------------- worker -------------------------------------
def process_series(
    uid: str,
    example_path: Path,
    overwrite: bool,
    verbose: bool,
) -> Tuple[str, int]:
    count = 0
    if overwrite:
        for old in PREVIEWS_DIR.glob(f"{uid}_slice*.webp"):
            old.unlink(missing_ok=True)
        (PREVIEWS_DIR / f"{uid}.json").unlink(missing_ok=True)
    elif (PREVIEWS_DIR / f"{uid}_slice0.webp").exists():
        return uid, 0

    series_dir = example_path.parent
    if not series_dir.exists():
        if verbose:
            print(f"[WARN] {uid}: directory not found {series_dir}")
        return uid, 0

    files = sorted(p for p in series_dir.iterdir()
                   if p.suffix.lower() in {".dcm", ".ima"} and p.is_file())
    if not files:
        if verbose:
            print(f"[WARN] {uid}: no DICOMs")
        return uid, 0

    for i, idx in enumerate(choose_indices(len(files))):
        src, dst = files[idx], PREVIEWS_DIR / f"{uid}_slice{i}.webp"
        try:
            ds = dcmread(src, force=True)
            if "PixelData" not in ds or getattr(ds, "SamplesPerPixel", 1) not in (1, 3):
                raise AttributeError("no usable PixelData")
            save_slice(ds, dst)
            count += 1
        except (InvalidDicomError, AttributeError, NotImplementedError,
                OSError, ValueError) as exc:
            if verbose:
                print(f"[SKIP] {uid} {src.name}: {exc}")
        except Exception as exc:
            if verbose:
                traceback.print_exc()
                print(f"[FAIL] {uid} {src.name}: {exc}")

    if count:
        meta = dict(uid=uid, total=len(files), written=count, folder=str(series_dir))
        (PREVIEWS_DIR / f"{uid}.json").write_text(json.dumps(meta))
    return uid, count
# ------------------------------------------------------------------------


# --------------------------- main ---------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Generate WebP previews into ./previews/")
    ap.add_argument("--dicom", required=True, type=Path,
                    help="DICOM root (unused; kept for symmetry)")
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    PREVIEWS_DIR.mkdir(exist_ok=True)

    series_map = load_manifest()
    print(f"[INFO] {len(series_map):,} series in manifest")

    max_workers = max(4, multiprocessing.cpu_count() * 2)
    written_total, skipped = 0, 0

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(process_series, uid, ex_path,
                               args.overwrite, args.verbose)
                   for uid, ex_path in series_map.items()]
        for fut in tqdm(as_completed(futures), total=len(futures),
                        desc="Generating previews"):
            _uid, n = fut.result()
            if n:
                written_total += n
            else:
                skipped += 1

    print(f"Wrote {written_total} WebP slices "
          f"({len(series_map) - skipped} series). {skipped} series skipped.")
    print("Output:", PREVIEWS_DIR.resolve())


if __name__ == "__main__":
    main()
