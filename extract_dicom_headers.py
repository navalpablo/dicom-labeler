#!/usr/bin/env python3
"""
extract_dicom_headers.py
------------------------

• One row per SeriesInstanceUID
• Adds "Example File" (first slice path)
• Adds "Plane Orientation"  (axial / coronal / sagittal / oblique / '')
  – finds ImageOrientationPatient in classic *and* enhanced multi‑frame
  – fallback 1: infers from PatientOrientation (0020,0020)
  – fallback 2: infers from ImagePositionPatient changes across slices
• Keeps/merges existing Annotation column if TSV already exists
"""
from __future__ import annotations

import argparse, csv, os, sys
from collections import OrderedDict, defaultdict
from pathlib import Path
from typing import Dict, Tuple, List, Optional

import numpy as np
import pydicom
from pydicom.errors import InvalidDicomError
from tqdm import tqdm

SCRIPT_DIR   = Path(__file__).resolve().parent
DEFAULT_TSV  = SCRIPT_DIR / "series_info.tsv"        # <── hard‑coded

# -------------------------------------------------------------------- #
# Tag → column‑name mapping
# -------------------------------------------------------------------- #
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
        ("00200037", "Image Orientation (Patient)"),   # for single‑frame
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
FIELDS = list(DICOM_FIELD_MAPPING.keys())

EXAMPLE_COL = "Example File"
PLANE_COL   = "Plane Orientation"
HEADER_ROW  = list(DICOM_FIELD_MAPPING.values()) + [EXAMPLE_COL, PLANE_COL, "Annotation"]

# -------------------------------------------------------------------- #
# Orientation → plane helper
# -------------------------------------------------------------------- #
def determine_plane(ori: List[float]) -> str:
    if len(ori) != 6:
        return ""
    row, col = ori[:3], ori[3:]
    normal = np.cross(row, col)
    idx = int(np.argmax(np.abs(normal)))  # 0=x,1=y,2=z
    major = np.abs(normal[idx])
    if major < 0.8:  # > ~36° from any axis → oblique
        return "oblique"
    return ("sagittal", "coronal", "axial")[idx]

def infer_plane_from_patient_orientation(po: str) -> str:
    if not po:
        return ""
    parts = po.upper().split('\\')
    if len(parts) != 2:
        return ""
    row_dir = parts[0][0] if len(parts[0]) > 0 else ''
    col_dir = parts[1][0] if len(parts[1]) > 0 else ''
    if not row_dir or not col_dir:
        return ""
    if row_dir in 'RL' and col_dir in 'AP':
        return "axial"
    if row_dir in 'RL' and col_dir in 'HF':
        return "coronal"
    if row_dir in 'AP' and col_dir in 'HF':
        return "sagittal"
    # Negate directions or combinations might still fit
    if row_dir in 'AP' and col_dir in 'RL':
        return "axial"  # rotated axial
    if row_dir in 'HF' and col_dir in 'RL':
        return "coronal"  # rotated
    if row_dir in 'HF' and col_dir in 'AP':
        return "sagittal"  # rotated
    return "oblique"

def _orientation_from_ds(ds: pydicom.Dataset) -> Optional[List[float]]:
    """Return 6 floats if found (classic or enhanced)."""
    # 1) single‑frame
    elem = ds.get((0x0020, 0x0037))
    if elem and len(elem.value) == 6:
        return [float(v) for v in elem.value]

    # 2) Enhanced MR/CT: Shared FG
    sfg = ds.get("SharedFunctionalGroupsSequence")
    if sfg:
        try:
            ori = sfg[0].PlaneOrientationSequence[0].ImageOrientationPatient
            if len(ori) == 6:
                return [float(v) for v in ori]
        except Exception:
            pass

    # 3) Per‑frame FG (fallback)
    pfg = ds.get("PerFrameFunctionalGroupsSequence")
    if pfg:
        try:
            ori = pfg[0].PlaneOrientationSequence[0].ImageOrientationPatient
            if len(ori) == 6:
                return [float(v) for v in ori]
        except Exception:
            pass
    return None

# -------------------------------------------------------------------- #
def hex_tag(h: str) -> Tuple[int, int]:
    return int(h[:4], 16), int(h[4:], 16)

def extract_header(fp: Path) -> Tuple[Dict[str, str], Optional[List[float]], int]:
    try:
        ds = pydicom.dcmread(fp, stop_before_pixels=True, force=True)
    except (InvalidDicomError, OSError):
        return {}, None, 0
    out: Dict[str, str] = {}
    for key in FIELDS:
        elem = ds.get(hex_tag(key))
        out[key] = str(elem.value) if elem else ""

    ori = _orientation_from_ds(ds)
    plane = determine_plane(ori) if ori else ""
    if not plane:
        po = ''
        if 'PatientOrientation' in ds:
            po_val = ds.PatientOrientation
            po = '\\'.join(po_val) if isinstance(po_val, (list, tuple)) else str(po_val)
        plane = infer_plane_from_patient_orientation(po)
    out[PLANE_COL] = plane

    pos_elem = ds.get((0x0020, 0x0032))
    pos = [float(v) for v in pos_elem.value] if pos_elem and len(pos_elem.value) == 3 else None

    inst = int(ds.get((0x0020, 0x0013)).value) if ds.get((0x0020, 0x0013)) else 0

    return out, pos, inst

def find_files(root: Path, read_all: bool):
    for d, _, files in os.walk(root):
        files.sort()
        for f in (files if read_all else files[:5]):
            yield Path(d) / f

# -------------------------------------------------------------------- #
def build_series_manifest(root: Path, read_all: bool):
    manifest: Dict[Tuple[str,str], Dict[str,str]] = OrderedDict()
    pos_per_series: defaultdict[Tuple[str,str], List[Tuple[int, List[float]]]] = defaultdict(list)

    for fp in tqdm(list(find_files(root, read_all)), desc="Reading headers"):
        info, pos, inst = extract_header(fp)
        if not info:
            continue
        study_uid = info.get("0020000D", "")
        series_uid = info.get("0020000E", "")
        if not (study_uid and series_uid):
            continue
        key = (study_uid, series_uid)
        if key not in manifest:
            info[EXAMPLE_COL] = str(fp)
            manifest[key] = info
        if pos:
            pos_per_series[key].append((inst, pos))

    # Fallback inference for series without plane (position changes)
    for key, row in manifest.items():
        if row[PLANE_COL]:
            continue  # already set
        poss = pos_per_series[key]
        if len(poss) < 2:
            continue
        poss.sort(key=lambda x: x[0])
        p1 = np.array(poss[0][1])
        p2 = np.array(poss[1][1])
        delta = p2 - p1
        if np.linalg.norm(delta) == 0:
            continue
        norm_delta = delta / np.linalg.norm(delta)
        idx = int(np.argmax(np.abs(norm_delta)))
        major = np.abs(norm_delta[idx])
        if major < 0.8:
            row[PLANE_COL] = "oblique"
        else:
            row[PLANE_COL] = ("sagittal", "coronal", "axial")[idx]

    return manifest

def merge_existing(out_tsv: Path, fresh):
    if not out_tsv.exists():
        return fresh
    merged = fresh.copy()
    with out_tsv.open(newline="") as f:
        rdr = csv.DictReader(f, delimiter="\t")
        for row in rdr:
            key = (row.get("Study Instance UID") or row.get("0020000D",""),
                   row.get("Series Instance UID") or row.get("0020000E",""))
            if key not in merged:
                merged[key] = {tag: row.get(DICOM_FIELD_MAPPING[tag], "")
                               for tag in FIELDS}
            merged[key][EXAMPLE_COL] = row.get(EXAMPLE_COL, merged[key].get(EXAMPLE_COL,""))
            merged[key][PLANE_COL]   = row.get(PLANE_COL,   merged[key].get(PLANE_COL,""))
            merged[key]["Annotation"]= row.get("Annotation","")
    return merged

def write_manifest(data, out_tsv: Path):
    with out_tsv.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HEADER_ROW, delimiter="\t")
        w.writeheader()
        for info in data.values():
            row = {DICOM_FIELD_MAPPING[k]: info.get(k,"") for k in FIELDS}
            row[EXAMPLE_COL] = info.get(EXAMPLE_COL,"")
            row[PLANE_COL]   = info.get(PLANE_COL,"")
            row["Annotation"]= info.get("Annotation","")
            w.writerow(row)

# -------------------------------------------------------------------- #
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Extract one row per DICOM series; output fixed to series_info.tsv",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--dicom", required=True, type=Path, help="DICOM root folder")
    p.add_argument("--read_all", action="store_true",
                   help="Inspect every file in each folder (slow)")
    return p.parse_args()


def main():
    args = parse_args()
    if not args.dicom.exists():
        sys.exit(f"[ERROR] DICOM root {args.dicom} not found")

    fresh = build_series_manifest(args.dicom, args.read_all)
    merged   = merge_existing(DEFAULT_TSV, fresh)
    write_manifest(merged, DEFAULT_TSV)

    print(f"✅  Manifest written to {DEFAULT_TSV.relative_to(SCRIPT_DIR)} "
          f"({len(merged):,} series)")
# -----------------------------------------------------------------------

if __name__ == "__main__":
    main()