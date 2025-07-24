"""
label_server package
====================

Provides a `create_app()` factory so you can:

    >>> from label_server import create_app
    >>> app = create_app()
    >>> app.run(debug=True)

The factory lets you override default file locations when unit-testing.
"""
from pathlib import Path
from .app import create_flask_app  # noqa: F401  (re-export)


def create_app(
    *,
    manifest_path: Path | str | None = None,
    labels_path: Path | str | None = None,
    previews_dir: Path | str | None = None,
):
    """
    Thin wrapper around `create_flask_app()` that resolves string → Path.

    All args are optional; if omitted, the defaults declared in `app.py`
    (`series_info.tsv`, `labels_config.txt`, `../previews`) are used.
    """
    return create_flask_app(
        manifest_path=Path(manifest_path).resolve() if manifest_path else None,
        labels_path=Path(labels_path).resolve() if labels_path else None,
        previews_dir=Path(previews_dir).resolve() if previews_dir else None,
    )
