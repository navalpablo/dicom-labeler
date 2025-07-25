#!/usr/bin/env python3
"""
safe_apply_labels.py
--------------------

Push labels from *series_info.tsv* back into every DICOM slice or move DELETE
series – thread-parallel, Windows-safe, no duplicate writes, no corruption.
"""

from __future__ import annotations
import argparse, csv, os, shutil, sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import pydicom

TAG_PROTOCOL = (0x0018, 0x1030)             # Protocol Name (LO, 64 B)
MANIFEST     = Path("series_info.tsv")      # label tool output


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #
def safe_protocol_name(old: str, annot: str, plane: str) -> str:
    """Keep the suffix intact, truncate the old part if needed."""
    suffix = f"_annot_{annot}_plane_{plane}"
    free   = 64 - len(suffix)
    base   = (old or "")[:max(free, 0)]
    return f"{base}{suffix}"


def handle_file(
    path: Path,
    rel: Path,
    uid_map: dict[str, tuple[str, str]],
    trash_root: Path,
) -> str:
    """
    Process one file:
      * move → returns 'moved'
      * edit → returns 'edited'
      * not in manifest → returns 'skipped'
    """
    try:
        ds = pydicom.dcmread(
            path,
            stop_before_pixels=True,           # fast — headers only
            specific_tags=["SeriesInstanceUID", "ProtocolName"],
        )
    except Exception as e:                     # unreadable file
        return f"error:{e}"

    uid = getattr(ds, "SeriesInstanceUID", None)
    if uid not in uid_map:
        return "skipped"

    annot, plane = uid_map[uid]
    if annot.upper() == "DELETE":
        dest = trash_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), dest)
        return "moved"

    new_proto = safe_protocol_name(
        str(getattr(ds, "ProtocolName", "")), annot, plane or "UNKNOWN"
    )
    if new_proto == getattr(ds, "ProtocolName", ""):
        return "unchanged"                     # already correct

    ds.ProtocolName = new_proto
    ds.save_as(path, write_like_original=False)
    return "edited"


# --------------------------------------------------------------------------- #
# Main                                                                         #
# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(
        description="Embed labels into DICOM headers or move DELETE series."
    )
    ap.add_argument("root", type=Path, help="DICOM root directory")
    ap.add_argument(
        "-j",
        "--threads",
        type=int,
        default=os.cpu_count(),
        help="Parallel threads (default: all CPUs)",
    )
    args = ap.parse_args()

    root: Path = args.root.resolve()
    if not root.is_dir():
        sys.exit(f"❌  {root} is not a directory")

    if not MANIFEST.exists():
        sys.exit(f"❌  Cannot find {MANIFEST}. Run extract_dicom_headers.py first.")

    # --------------------------- load TSV into memory -----------------------
    uid_map: dict[str, tuple[str, str]] = {}  # UID → (annotation, plane)
    with MANIFEST.open() as f:
        rdr = csv.DictReader(f, delimiter="\t")
        for row in rdr:
            uid_map[row["Series Instance UID"]] = (
                row.get("Annotation", "").strip(),
                row.get("Plane Orientation", "").strip(),
            )

    trash_root = root / "WAITING_DELETION"

    # ----------------------------- gather files ----------------------------
    all_files = [
        p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in {".dcm", ".ima", ""}
    ]
    print(f"📦  Found {len(all_files)} files, processing with {args.threads} threads…")

    # ----------------------------- parallel loop ---------------------------
    counts = {"edited": 0, "moved": 0, "skipped": 0, "unchanged": 0, "error": 0}
    with ThreadPoolExecutor(max_workers=max(1, args.threads)) as pool:
        fut2file = {
            pool.submit(
                handle_file,
                p,
                p.relative_to(root),
                uid_map,
                trash_root,
            ): p
            for p in all_files
        }
        for fut in as_completed(fut2file):
            status = fut.result()
            key = status.split(":", 1)[0]  # collapse 'error:...' → 'error'
            counts[key] = counts.get(key, 0) + 1

    # ----------------------------- summary ---------------------------------
    print(
        "✅  Done.\n"
        f"    edited   : {counts['edited']}\n"
        f"    moved    : {counts['moved']}\n"
        f"    unchanged: {counts['unchanged']}\n"
        f"    skipped  : {counts['skipped']}\n"
        f"    errors   : {counts['error']}"
    )


if __name__ == "__main__":
    main()
