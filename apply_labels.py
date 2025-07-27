#!/usr/bin/env python3
"""
apply_labels.py
--------------------

Embed labels from series_info.tsv into DICOM headers (or move DELETE series)
without corrupting pixel data.

Features
--------
* Adds acquisition dimension tag: 2D / 3D / 4D based on:
    - 4D: NumberOfTemporalPositions > 1
    - 3D: SliceThickness < 2 mm OR SpacingBetweenSlices < 2 mm
    - 2D: otherwise
* Prefix format:
    seq_{annotation}_acq_{dim}_plane_{plane}___{original_protocol_name}
* Maintains 64‑byte limit for ProtocolName.
* Optional --dry-run and --logfile.
* Multithreaded processing with optional tqdm progress bar.
* Atomic save to temporary file to avoid deferred‑read issues.
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import shutil
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pydicom

# optional progress bar
try:
    from tqdm import tqdm  # type: ignore
except ImportError:  # pragma: no cover
    tqdm = None

MANIFEST = Path("series_info.tsv")
PROTO_MAX_LEN = 64

# --------------------------------------------------------------------------- #
# Helper functions                                                            #
# --------------------------------------------------------------------------- #

def configure_logger(logfile: Path | None) -> logging.Logger | None:
    if logfile is None:
        return None
    logfile.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=logfile,
        level=logging.INFO,
        format="%(asctime)s\t%(levelname)s\t%(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    return logging.getLogger("apply_labels")


def classify_acq_dim(ds) -> str:
    """Return '4D', '3D', or '2D'."""
    ntp = getattr(ds, "NumberOfTemporalPositions", None)
    if ntp not in (None, "", 0, "0"):
        try:
            if int(ntp) > 1:
                return "4D"
        except Exception:
            pass

    def to_float(val):
        try:
            return float(val)
        except Exception:
            return None

    st = to_float(getattr(ds, "SliceThickness", None))
    sb = to_float(getattr(ds, "SpacingBetweenSlices", None))
    if (st is not None and st < 2.0) or (sb is not None and sb < 2.0):
        return "3D"
    return "2D"


def build_protocol_name(orig: str, annot: str, dim: str, plane: str) -> str:
    prefix = f"seq_{annot}_acq_{dim}_plane_{plane}___"
    max_orig_len = PROTO_MAX_LEN - len(prefix)
    return f"{prefix}{(orig or '')[: max(max_orig_len, 0)]}"


# --------------------------------------------------------------------------- #
# Core worker                                                                 #
# --------------------------------------------------------------------------- #

def handle_file(
    path: Path,
    rel: Path,
    uid_map: dict[str, tuple[str, str]],
    trash_root: Path,
    dry_run: bool = False,
    logger: logging.Logger | None = None,
) -> str:
    """Process one DICOM file; returns a status key."""
    try:
        ds = pydicom.dcmread(path, defer_size=1024)
    except Exception as exc:
        if logger:
            logger.error("error\t%s\t%s", rel, exc)
        return "error"

    uid = getattr(ds, "SeriesInstanceUID", None)
    if uid not in uid_map:
        return "skipped"

    annot, plane = uid_map[uid]
    if (annot or "").upper() == "DELETE":
        if dry_run:
            if logger:
                logger.info("dry-move\t%s", rel)
            return "moved"
        dest = trash_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), dest)
        if logger:
            logger.info("move\t%s\t->\t%s", rel, dest.relative_to(trash_root))
        return "moved"

    dim = classify_acq_dim(ds)
    new_proto = build_protocol_name(
        str(getattr(ds, "ProtocolName", "")),
        annot or "UNKNOWN",
        dim,
        plane or "UNKNOWN",
    )
    if new_proto == getattr(ds, "ProtocolName", ""):
        return "unchanged"

    if dry_run:
        if logger:
            logger.info("dry-edit\t%s\t%s", rel, new_proto)
        return "edited"

    ds.ProtocolName = new_proto

    # atomic save
    fh, tmp_name = tempfile.mkstemp(suffix=".dcm", dir=str(path.parent))
    os.close(fh)
    try:
        ds.save_as(tmp_name, write_like_original=True)
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)

    if logger:
        logger.info("edit\t%s\t%s", rel, new_proto)
    return "edited"


# --------------------------------------------------------------------------- #
# Main                                                                        #
# --------------------------------------------------------------------------- #

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Embed labels into DICOM headers or move DELETE series."
    )
    parser.add_argument("root", type=Path, help="DICOM root directory")
    parser.add_argument(
        "-j", "--threads", type=int, default=os.cpu_count(),
        help="Parallel threads (default: all CPUs)",
    )
    parser.add_argument(
        "-n", "--dry-run", action="store_true",
        help="Simulate actions without modifying files",
    )
    parser.add_argument(
        "-l", "--logfile", type=Path,
        help="Write actions log to this file (TSV)",
    )
    args = parser.parse_args()

    root = args.root.resolve()
    if not root.is_dir():
        sys.exit(f"{root} is not a directory")

    if not MANIFEST.exists():
        sys.exit("series_info.tsv not found – run extract_dicom_headers.py first")

    logger = configure_logger(args.logfile)

    # load manifest
    uid_map: dict[str, tuple[str, str]] = {}
    with MANIFEST.open() as f:
        for row in csv.DictReader(f, delimiter="\t"):
            uid_map[row["Series Instance UID"]] = (
                row.get("Annotation", "").strip(),
                row.get("Plane Orientation", "").strip(),
            )

    trash_root = root / "WAITING_DELETION"

    # gather files
    all_files = [
        p for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in {".dcm", ".ima", ""}
    ]
    print(
        f"Found {len(all_files)} files. Processing with {args.threads} threads"
        f"{' (dry-run)' if args.dry_run else ''}."
    )

    counts = {k: 0 for k in ("edited", "moved", "skipped", "unchanged", "error")}
    progress = tqdm(total=len(all_files), unit="file") if tqdm else None

    with ThreadPoolExecutor(max_workers=max(1, args.threads)) as pool:
        futures = {
            pool.submit(
                handle_file,
                p,
                p.relative_to(root),
                uid_map,
                trash_root,
                args.dry_run,
                logger,
            ): p
            for p in all_files
        }
        for fut in as_completed(futures):
            counts[fut.result()] += 1
            if progress:
                progress.update(1)

    if progress:
        progress.close()

    # summary
    print(
        "Done.\n"
        f"    edited   : {counts['edited']}\n"
        f"    moved    : {counts['moved']}\n"
        f"    unchanged: {counts['unchanged']}\n"
        f"    skipped  : {counts['skipped']}\n"
        f"    errors   : {counts['error']}"
    )
    if args.dry_run:
        print("Dry-run mode: no files were modified.")


if __name__ == "__main__":
    main()
