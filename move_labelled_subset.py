#!/usr/bin/env python3
r"""
find_by_protocol.py
===================

Copy every DICOM whose (0018,1030) *Protocol Name* contains at least one
of the supplied search terms (case-insensitive). Only the header is read
(`stop_before_pixels=True`) for speed.

Examples
--------
1.  Three simple terms, given as one   **-t**   flag (comma-separated):
    python move_labelled_subset.py -t CT,T1,T2         /in_dir  /out_dir

2.  Terms that include **spaces** or **slashes** →
    quote each one or use multiple  -t:
    python move_labelled_subset.py \
        -t "T1 weighted" \
        -t "Scout/Localizer" \
        -t "3D-FLAIR" \
        "/mnt/dicom source" "/mnt/output subset"

3.  Windows CMD requires double quotes:
    move_labelled_subset.py -t "Cardiac Cine" -t "T2* Map" C:\\data C:\\subset

4.  A single term that itself contains a comma:
    (either escape the comma or use a second  -t)
    python move_labelled_subset.py -t "Scout\\,Localizer"  src dst
    python move_labelled_subset.py -t "Scout/Localizer" -t "3D, Sag" src dst

Notes
-----
* Repeat **-t/--term** as often as needed; each flag may itself hold
  one *or* a comma-separated list.
* On PowerShell, use single quotes:  -t 'T1 weighted'.
* Special characters other than the shell’s own metacharacters are
  passed through unchanged (e.g. “+”, “-”, “_”, “*”, “#”).
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import os
import shutil
import sys
from pathlib import Path
from typing import Iterable, Sequence

import pydicom
from pydicom.errors import InvalidDicomError

# ---------------------------------------------------------------------------#
# Optional progress bar: use tqdm if available and tty is interactive
try:
    from tqdm import tqdm
except ImportError:
    tqdm = None  # falls back to plain output
# ---------------------------------------------------------------------------#

TAG_PROTOCOL_NAME = (0x0018, 0x1030)  # (0018,1030) Protocol Name


def parse_terms(raw: str) -> list[str]:
    """Split one -t value on *unescaped* commas; trim whitespace."""
    out: list[str] = []
    current: list[str] = []
    escape = False
    for ch in raw:
        if escape:
            current.append(ch)
            escape = False
        elif ch == "\\":
            escape = True
        elif ch == ",":
            term = "".join(current).strip()
            if term:
                out.append(term)
            current = []
        else:
            current.append(ch)
    term = "".join(current).strip()
    if term:
        out.append(term)
    return out


def iter_dicom_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*"):
        if path.is_file():
            yield path


def match_protocol(ds: pydicom.dataset.Dataset, terms: Sequence[str]) -> bool:
    if TAG_PROTOCOL_NAME not in ds:
        return False
    proto = str(ds[TAG_PROTOCOL_NAME].value or "").lower()
    return any(term.lower() in proto for term in terms)


def copy_if_match(
    path: Path, src_root: Path, dst_root: Path, terms: Sequence[str]
) -> bool:
    try:
        ds = pydicom.dcmread(path, stop_before_pixels=True)
    except (InvalidDicomError, FileNotFoundError, PermissionError):
        return False

    if not match_protocol(ds, terms):
        return False

    rel = path.relative_to(src_root)
    target = dst_root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, target)
    return True


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Copy DICOMs whose ProtocolName matches any search term."
    )
    parser.add_argument("src", type=Path, help="input root directory")
    parser.add_argument("dst", type=Path, help="output root directory (created if needed)")
    parser.add_argument(
        "-t",
        "--term",
        required=True,
        action="append",
        metavar="STR[,STR...]",
        help=(
            "search term; repeat -t or separate with commas. "
            "Escape a comma inside a term with '\\,'."
        ),
    )
    parser.add_argument(
        "-j",
        "--jobs",
        type=int,
        default=os.cpu_count() or 4,
        help="parallel workers (default: number of CPUs)",
    )
    args = parser.parse_args(argv)

    terms: list[str] = []
    for raw in args.term:
        terms.extend(parse_terms(raw))
    if not terms:
        parser.error("No valid search terms supplied with -t/--term")

    args.dst.mkdir(parents=True, exist_ok=True)

    files = list(iter_dicom_files(args.src))
    total = len(files)
    copied = 0

    # progress bar (only if tqdm present *and* stderr is a tty)
    progress = (
        tqdm(total=total, unit="file", desc="Copying", ncols=80)
        if tqdm and sys.stderr.isatty()
        else None
    )

    with cf.ThreadPoolExecutor(max_workers=args.jobs) as exe:
        futures = (
            exe.submit(copy_if_match, p, args.src, args.dst, terms) for p in files
        )
        for done in cf.as_completed(futures):
            copied += bool(done.result())
            if progress:
                progress.update()

    if progress:
        progress.close()

    print(f"Scanned {total} files, copied {copied} matching DICOMs.")


if __name__ == "__main__":
    main()
