"""
label_server.app
================

Flask web UI for step 3–4 of the DICOM-labeling pipeline.

Run it from the project root:

    $ export FLASK_APP=label_server.app
    $ flask run          # then open http://localhost:5000/

* GET  /page/<n>     – show n-th batch (20 series / page)
* POST /save_page/<n> – store annotations from that batch
* GET  /previews/<file.webp> – serve thumbnail images

The Jinja templates live in `label_server/templates/`:
    base.html      – common header/nav
    page.html      – loops through 20 series on a page

Static assets (CSS) are in `label_server/static/`.
"""
from __future__ import annotations

import csv
import os
from pathlib import Path

from flask import (
    Flask,
    render_template,
    redirect,
    request,
    send_from_directory,
    url_for,
    flash,
)

###############################################################################
# Configuration (defaults – can be overridden in create_flask_app())
###############################################################################

DEFAULT_MANIFEST = Path("series_info.tsv")
DEFAULT_LABELS = Path("labels_config.txt")
DEFAULT_PREVIEWS = Path("previews")  # sibling of project root


###############################################################################
# Data-file helpers
###############################################################################


def load_manifest(tsv_path: Path) -> list[dict[str, str]]:
    """Read TSV into a list[dict]; header row → dict keys."""
    rows: list[dict[str, str]] = []
    if not tsv_path.exists():
        return rows
    with tsv_path.open(newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        rows.extend(reader)
    return rows


def save_manifest(tsv_path: Path, rows: list[dict[str, str]]):
    """Overwrite TSV in place, preserving column order."""
    if not rows:
        return
    fieldnames = list(rows[0].keys())
    tmp = tsv_path.with_suffix(".tmp")
    with tmp.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(tsv_path)


def load_labels(label_path: Path) -> list[str]:
    """Return label list (duplicates stripped, order preserved)."""
    seen: set[str] = set()
    labels: list[str] = []
    if label_path.exists():
        for line in label_path.read_text().splitlines():
            l = line.strip()
            if l and l not in seen:
                labels.append(l)
                seen.add(l)
    return labels


def append_label(label_path: Path, new_label: str):
    """Add *new_label* to file if it isn't already present."""
    new_label = new_label.strip()
    if not new_label:
        return
    existing = load_labels(label_path)
    if new_label not in existing:
        with label_path.open("a") as f:
            f.write(new_label + "\n")


###############################################################################
# Factory
###############################################################################


def create_flask_app(
    *,
    manifest_path: Path | None = None,
    labels_path: Path | None = None,
    previews_dir: Path | None = None,
) -> Flask:
    """Return a configured Flask app instance."""
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
        static_url_path="/static",
    )
    # Unsigned cookies OK (local use), but Flask still needs a secret key.
    app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-only-secret")

    # Resolve file paths (relative → absolute)
    app.config["MANIFEST_PATH"] = Path(manifest_path or DEFAULT_MANIFEST).resolve()
    app.config["LABELS_PATH"] = Path(labels_path or DEFAULT_LABELS).resolve()
    app.config["PREVIEWS_DIR"] = Path(previews_dir or DEFAULT_PREVIEWS).resolve()

    ###########################################################################
    # Routes
    ###########################################################################

    @app.route("/")
    def index():
        """Redirect / → first page (1-indexed)."""
        return redirect(url_for("page", page_num=1))

    @app.route("/page/<int:page_num>")
    def page(page_num: int):
        """
        Render page *page_num* (1-indexed), 20 series per page.
        """
        manifest = load_manifest(app.config["MANIFEST_PATH"])
        per_page = 20
        total_pages = (len(manifest) + per_page - 1) // per_page

        # Bounds check
        if page_num < 1 or page_num > max(total_pages, 1):
            return redirect(url_for("page", page_num=1))

        start = (page_num - 1) * per_page
        end = start + per_page
        series_batch = manifest[start:end]

        labels = load_labels(app.config["LABELS_PATH"])

        # Progress counters
        done = sum(1 for row in manifest if row.get("Annotation"))
        remaining = len(manifest) - done

        return render_template(
            "page.html",
            page_num=page_num,
            total_pages=total_pages,
            series_batch=series_batch,
            labels=labels,
            done=done,
            remaining=remaining,
        )

    @app.route("/save_page/<int:page_num>", methods=["POST"])
    def save_page(page_num: int):
        """
        Handle form submission from page <page_num>; write annotations back
        into manifest TSV and append new labels to labels_config.txt.
        """
        form = request.form  # ImmutableMultiDict
        annotations: dict[str, str] = {}
        for key, value in form.items():
            if key.startswith("label_"):
                series_uid = key.removeprefix("label_")
                annotations[series_uid] = value.strip()

        if not annotations:
            flash("No annotations submitted.", "warning")
            return redirect(url_for("page", page_num=page_num))

        # Load → update → save manifest
        manifest = load_manifest(app.config["MANIFEST_PATH"])
        changed = 0
        for row in manifest:
            uid = row.get("Series Instance UID") or row.get("0020000E", "")
            if uid in annotations and annotations[uid]:
                if row.get("Annotation") != annotations[uid]:
                    row["Annotation"] = annotations[uid]
                    changed += 1
                    # If label is new, add to config
                    append_label(app.config["LABELS_PATH"], annotations[uid])

        if changed:
            save_manifest(app.config["MANIFEST_PATH"], manifest)
            flash(f"Saved {changed} annotation(s).", "success")
        else:
            flash("Nothing new to save.", "info")

        # After save, redirect to *next* page (wrap to 1 if last)
        next_page = page_num + 1
        total_pages = (len(manifest) + 19) // 20
        if next_page > total_pages:
            next_page = 1
        return redirect(url_for("page", page_num=next_page))

    @app.route("/previews/<path:filename>")
    def previews(filename: str):
        """
        Serve WebP thumbnails from the previews directory.
        """
        return send_from_directory(app.config["PREVIEWS_DIR"], filename)

    return app


###############################################################################
# Allow  `python -m label_server.app`  as a quick entry-point
###############################################################################

if __name__ == "__main__":  # pragma: no cover
    app = create_flask_app()
    app.run(debug=True)
