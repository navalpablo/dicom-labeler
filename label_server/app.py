#!/usr/bin/env python3
"""
label_server.app  –  Study‑centric Flask UI
==========================================

Routes
------
GET  /page/<n>     – all series for the n‑th StudyInstanceUID
POST /save_page/<n> – store annotations
GET  /previews/<fname.webp> – serve thumbnails
"""
from __future__ import annotations

import csv, os
from collections import OrderedDict, defaultdict
from pathlib import Path
from typing import List

from flask import (
    Flask, render_template, redirect, request,
    send_from_directory, url_for, flash
)

# ------------------------------------------------------------------ #
# Defaults – override via factory args
# ------------------------------------------------------------------ #
DEFAULT_MANIFEST = Path("series_info.tsv")
DEFAULT_LABELS   = Path("labels_config.txt")
DEFAULT_PREVIEWS = Path("previews")

# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #
def load_manifest(tsv: Path) -> List[dict[str, str]]:
    if not tsv.exists():
        return []
    with tsv.open(newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))

def save_manifest(tsv: Path, rows: List[dict[str, str]]):
    if not rows:
        return
    tmp = tsv.with_suffix(".tmp")
    with tmp.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys(), delimiter="\t")
        w.writeheader(); w.writerows(rows)
    tmp.replace(tsv)

def load_labels(path: Path) -> List[str]:
    if not path.exists():
        return []
    out, seen = [], set()
    for l in path.read_text().splitlines():
        l = l.strip()
        if l and l not in seen:
            out.append(l); seen.add(l)
    return out

def append_label(path: Path, new: str):
    new = new.strip()
    if new and new not in load_labels(path):
        path.write_text(path.read_text() + new + "\n" if path.exists() else new + "\n")

# ------------------------------------------------------------------ #
# Factory
# ------------------------------------------------------------------ #
def create_flask_app(*, manifest_path: Path | None = None,
                     labels_path:   Path | None = None,
                     previews_dir:  Path | None = None) -> Flask:
    app = Flask(__name__, template_folder="templates",
                static_folder="static", static_url_path="/static")
    app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev‑only")

    app.config.update(
        MANIFEST_PATH = Path(manifest_path or DEFAULT_MANIFEST).resolve(),
        LABELS_PATH   = Path(labels_path   or DEFAULT_LABELS  ).resolve(),
        PREVIEWS_DIR  = Path(previews_dir  or DEFAULT_PREVIEWS).resolve(),
    )

    # ------------------------------------------------------------------ #
    # Routes
    # ------------------------------------------------------------------ #
    @app.route("/")
    def index():
        return redirect(url_for("page", page_num=1))

    @app.route("/page/<int:page_num>")
    def page(page_num: int):
        rows   = load_manifest(app.config["MANIFEST_PATH"])
        labels = load_labels(app.config["LABELS_PATH"])

        # --- group by Study UID ------------------------------------------------
        studies: dict[str, list[dict[str, str]]] = defaultdict(list)
        for r in rows:
            studies[r["Study Instance UID"]] .append(r)
        study_ids = sorted(studies)
        total_pages = len(study_ids) or 1

        if page_num < 1 or page_num > total_pages:
            return redirect(url_for("page", page_num=1))

        study_uid   = study_ids[page_num-1]
        series_batch = studies[study_uid]

        done       = sum(1 for r in rows if r.get("Annotation"))
        remaining  = len(rows) - done

        return render_template(
            "page.html",
            page_num=page_num, total_pages=total_pages,
            study_uid=study_uid, series_batch=series_batch,
            labels=labels, done=done, remaining=remaining
        )

    @app.route("/save_page/<int:page_num>", methods=["POST"])
    def save_page(page_num: int):
        form = request.form
        annos = {k.removeprefix("label_"): v.strip()
                 for k, v in form.items() if k.startswith("label_") and v.strip()}

        if not annos:
            flash("No annotations submitted.", "warning")
            return redirect(url_for("page", page_num=page_num))

        rows = load_manifest(app.config["MANIFEST_PATH"])
        changed = 0
        for r in rows:
            uid = r.get("Series Instance UID")
            if uid in annos and r.get("Annotation") != annos[uid]:
                r["Annotation"] = annos[uid]; changed += 1
                append_label(app.config["LABELS_PATH"], annos[uid])

        if changed:
            save_manifest(app.config["MANIFEST_PATH"], rows)
            flash(f"Saved {changed} annotation(s).", "success")
        else:
            flash("Nothing new to save.", "info")

        next_page = page_num + 1
        if next_page > (len(set(r["Study Instance UID"] for r in rows)) or 1):
            next_page = 1
        return redirect(url_for("page", page_num=next_page))

    @app.route("/previews/<path:filename>")
    def previews(filename: str):
        return send_from_directory(app.config["PREVIEWS_DIR"], filename)

    return app

# ------------------------------------------------------------------ #
if __name__ == "__main__":      # dev shortcut
    create_flask_app().run(debug=True)