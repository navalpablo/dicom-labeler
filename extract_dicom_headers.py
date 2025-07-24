#!/usr/bin/env python3
"""
extract_dicom_headers.py
------------------------

Step 1 of the DICOM-labeling pipeline.

Usage
-----
    python extract_dicom_headers.py \
        --dicom /path/to/dicom_root \
        --output series_info.tsv

    # Read every DICOM file (slower but safest)
    python extract_dicom_headers.py \
        --dicom /path/to/dicom_root \
        --output series_info.tsv \
        --read_all
"""
import argparse
import csv
import os
import sys
from collections import OrderedDict, defaultdict
from pathlib import Path
from typing import Dict, Iterator, List, Tuple

import pydicom
from pydicom.errors import InvalidDicomError
from tqdm import tqdm


###############################################################################
# Configuration
###############################################################################

# Mapping from tag hex string → human-readable column name.
# Feel free to add/remove as needed.
DICOM_FIELD_MAPPING: Dict[str, str] = OrderedDict(
    [
        ("00100010", "Patient Name"),
        ("00100030", "Patient Birth Date"),
        ("00100040", "Patient Sex"),
        ("00101010", "Patient Age"),
        ("00100020", "Patient ID"),
        ("00080070", "Manufacturer"),
        ("00081090", "Manufacturer's Model Name"),
        ("00181030", "Protocol Name"),
        ("00189423", "Acquisition Protocol Name"),
        ("00080020", "Study Date"),
        ("00180087", "Magnetic Field Strength"),
        ("00080080", "Institution Name"),
        ("00080050", "Accession Number"),
        ("0020000D", "Study Instance UID"),
        ("00200011", "Series Number"),
        ("0008103E", "Series Description"),
        ("0020000E", "Series Instance UID"),
        ("00540081", "Number of Slices"),
        ("00181310", "Acquisition Matrix"),
        ("00280030", "Pixel Spacing"),
        ("00180088", "Spacing Between Slices"),
        ("00180050", "Slice Thickness"),
        ("00180080", "Repetition Time"),
        ("00180081", "Echo Time"),
        ("00180086", "Echo Number(s)"),
        ("00180091", "Echo Train Length"),
        ("00180082", "Inversion Time"),
        ("00181314", "Flip Angle"),
        ("00080008", "Image Type"),
        ("00189073", "Acquisition Duration"),
        ("2001101B", "Prepulse Delay"),
        ("00201209", "Number Series Related Instances"),
    ]
)

FIELDS: List[str] = list(DICOM_FIELD_MAPPING.keys())
HEADER_ROW: List[str] = list(DICOM_FIELD_MAPPING.values()) + ["Annotation"]


###############################################################################
# Helpers
###############################################################################


def hex_string_to_tag(hex_str: str) -> Tuple[int, int]:
    """Convert a 8-char hex string (e.g. '00080020') to (group, element) ints."""
    group, element = hex_str[:4], hex_str[4:]
    return int(group, 16), int(element, 16)


def find_dicom_files(root_dir: Path, read_all: bool) -> Iterator[Path]:
    """
    Walk *root_dir* and yield DICOM file paths.

    We assume a series lives inside its own folder; only one file is needed
    per series for header extraction unless --read_all is passed.
    """
    for dirpath, _dirs, files in os.walk(root_dir):
        # Heuristic: stop descending once we find DICOMs in a leaf folder.
        # Grab the first file(s) in this folder, not all descendant folders.
        if files:
            file_paths = [Path(dirpath) / f for f in files]
            file_paths.sort()  # deterministic order
            for fp in (file_paths if read_all else file_paths[:5]):
                yield fp


def extract_header_fields(dcm_path: Path, fields: List[str]) -> Dict[str, str]:
    """
    Read *dcm_path* header (skip pixels) and return {tag_hex: value}.
    Missing tags → ''.
    """
    try:
        ds = pydicom.dcmread(dcm_path, stop_before_pixels=True, force=True)
    except (InvalidDicomError, OSError) as exc:
        print(f"[WARN] Skipping {dcm_path}: {exc}", file=sys.stderr)
        return {}

    info: Dict[str, str] = {}
    for field in fields:
        tag = hex_string_to_tag(field)
        info[field] = str(ds.get(tag, ""))  # '' if missing
    return info


###############################################################################
# Main extraction routine
###############################################################################


def build_series_manifest(
    dicom_root: Path, read_all: bool
) -> Tuple[Dict[Tuple[str, str], Dict[str, str]], Dict[str, List[Path]]]:
    """
    Return:
        unique_sequences: {(StudyUID, SeriesUID): field_dict}
        series_to_filelist: {SeriesUID: [example file paths]}
    """
    unique_sequences: Dict[Tuple[str, str], Dict[str, str]] = OrderedDict()
    series_to_filelist: Dict[str, List[Path]] = defaultdict(list)

    files = list(find_dicom_files(dicom_root, read_all))
    for fp in tqdm(files, desc="Reading DICOM headers"):
        info = extract_header_fields(fp, FIELDS)
        if not info:
            continue

        study_uid = info.get("0020000D", "")
        series_uid = info.get("0020000E", "")

        if not (study_uid and series_uid):
            # Skip files lacking critical identifiers
            continue

        series_to_filelist[series_uid].append(fp)

        key = (study_uid, series_uid)
        if key not in unique_sequences:
            # Keep the first representative header we encounter
            unique_sequences[key] = info

    return unique_sequences, series_to_filelist


def merge_with_existing(
    output_tsv: Path, new_sequences: Dict[Tuple[str, str], Dict[str, str]]
) -> Dict[Tuple[str, str], Dict[str, str]]:
    """
    If *output_tsv* already exists, keep previous Annotation values.

    We return a merged dict that preserves any prior annotations.
    """
    if not output_tsv.exists():
        return new_sequences

    merged = new_sequences.copy()
    with output_tsv.open(newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            study_uid = row.get("Study Instance UID") or row.get("0020000D", "")
            series_uid = row.get("Series Instance UID") or row.get("0020000E", "")
            key = (study_uid, series_uid)
            if key not in merged:
                # Series disappeared from disk?  Keep anyway.
                merged[key] = {hex_key: row.get(DICOM_FIELD_MAPPING[hex_key], "")
                               for hex_key in FIELDS}
            # Carry over existing Annotation, if any
            annotation = row.get("Annotation", "")
            merged[key]["Annotation"] = annotation

    return merged


def write_manifest(manifest: Dict[Tuple[str, str], Dict[str, str]], output_tsv: Path):
    """Write TSV with header row (readable names) + Annotation column."""
    with output_tsv.open("w", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=HEADER_ROW, delimiter="\t", extrasaction="ignore"
        )
        writer.writeheader()
        for info in manifest.values():
            # Map hex tags → readable column names
            row = {DICOM_FIELD_MAPPING[tag]: info.get(tag, "") for tag in FIELDS}
            row.setdefault("Annotation", info.get("Annotation", ""))
            writer.writerow(row)


###############################################################################
# CLI
###############################################################################


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract one row per DICOM series and build/update series_info.tsv",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--dicom",
        required=True,
        type=Path,
        help="Root directory containing subject/studydate/series/* DICOM files",
    )
    parser.add_argument(
        "--output",
        default=Path("series_info.tsv"),
        type=Path,
        help="Destination TSV manifest",
    )
    parser.add_argument(
        "--read_all",
        action="store_true",
        help="Inspect every file (instead of first 5) in each series folder",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    if not args.dicom.exists():
        sys.exit(f"[ERROR] DICOM root {args.dicom} does not exist")

    # ------------------------------------------------------------
    # 1. Scan DICOMs → fresh manifest
    # ------------------------------------------------------------
    fresh_manifest, _series2files = build_series_manifest(args.dicom, args.read_all)

    # ------------------------------------------------------------
    # 2. Merge with previous annotations (if output exists)
    # ------------------------------------------------------------
    merged_manifest = merge_with_existing(args.output, fresh_manifest)

    # ------------------------------------------------------------
    # 3. Write out TSV
    # ------------------------------------------------------------
    write_manifest(merged_manifest, args.output)

    print(
        f"\n✅  Wrote {len(merged_manifest)} series rows to {args.output.resolve()}\n"
        "   Re-run this script any time you add more DICOMs — "
        "existing Annotation values are preserved."
    )


if __name__ == "__main__":
    main()
