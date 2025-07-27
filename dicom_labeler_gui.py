#!/usr/bin/env python3
"""dicom_labeler_gui.py – PyQt5 front‑end for the five‑step pipeline.

NEW IN THIS VERSION
-------------------
* Added **5 · Copy subset** button that wraps ``move_labelled_subset.py``.
  – Uses the DICOM root selected at the top as *input* directory.
  – Prompts for an *output* directory via ``QFileDialog``.
  – Prompts for one or more *search terms* via ``QInputDialog`` (comma‑separated).
  – Runs the subset script in the background and streams its stdout to the log.
"""
from __future__ import annotations

import os, sys, subprocess, threading, webbrowser
from pathlib import Path
from typing import List

from PyQt5.QtCore import Qt, pyqtSignal, QObject
from PyQt5.QtGui import QTextCursor
from PyQt5.QtWidgets import (
    QApplication, QFileDialog, QGridLayout, QHBoxLayout, QInputDialog,
    QLineEdit, QPushButton, QSizePolicy, QTextEdit, QVBoxLayout, QWidget,
)

# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent
PYTHON_EXE = Path(sys.executable).resolve()

EXTRACT_SCRIPT = REPO_ROOT / "extract_dicom_headers.py"
PREVIEW_SCRIPT = REPO_ROOT / "generate_previews.py"
APPLY_SCRIPT   = REPO_ROOT / "apply_labels.py"
SUBSET_SCRIPT  = REPO_ROOT / "move_labelled_subset.py"   # <── NEW

FLASK_APP_ENV  = "label_server.app:create_flask_app"
FLASK_PORT     = "5000"

# ---------------------------------------------------------------------------
class StreamReader(QObject):
    """Run a subprocess and stream stdout → Qt signal line‑by‑line."""
    new_line = pyqtSignal(str)
    finished = pyqtSignal(int)

    def __init__(self, cmd: List[str], cwd: Path):
        super().__init__()
        self.cmd, self.cwd = cmd, cwd
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def _run(self):
        try:
            proc = subprocess.Popen(
                self.cmd, cwd=self.cwd, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=1,
            )
        except FileNotFoundError as e:
            self.new_line.emit(f"[ERROR] {e}\n")
            self.finished.emit(1)
            return

        assert proc.stdout
        for line in proc.stdout:
            self.new_line.emit(line)
        proc.wait()
        self.finished.emit(proc.returncode)

# ---------------------------------------------------------------------------
class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("DICOM Series Labeler GUI")
        self.resize(900, 650)
        self._flask_proc: subprocess.Popen | None = None

        # DICOM root line + browse
        self.dicom_edit = QLineEdit(); self.dicom_edit.setPlaceholderText("Select DICOM root …")
        self.dicom_edit.textChanged.connect(self._validate)
        browse = QPushButton("Browse …"); browse.clicked.connect(self._browse)
        path_row = QHBoxLayout(); path_row.addWidget(self.dicom_edit, 1); path_row.addWidget(browse)

        # Stage buttons
        self.btn_extract  = QPushButton("1 · Extract headers")
        self.btn_preview  = QPushButton("2 · Generate previews")
        self.btn_annotate = QPushButton("3 · Open annotation UI")
        self.btn_apply    = QPushButton("4 · Apply labels to DICOMs")
        self.btn_subset   = QPushButton("5 · Copy subset")           # <── NEW
        for b in (self.btn_extract, self.btn_preview, self.btn_annotate, self.btn_apply, self.btn_subset):
            b.setEnabled(False)
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.btn_extract .clicked.connect(self._do_extract)
        self.btn_preview .clicked.connect(self._do_preview)
        self.btn_annotate.clicked.connect(self._do_flask)
        self.btn_apply   .clicked.connect(self._do_apply)
        self.btn_subset  .clicked.connect(self._do_subset)            # <── NEW
        col_btns = QVBoxLayout(); [col_btns.addWidget(b) for b in (
            self.btn_extract, self.btn_preview, self.btn_annotate, self.btn_apply, self.btn_subset
        )]

        # Log area
        self.log = QTextEdit(readOnly=True)
        self.log.setLineWrapMode(QTextEdit.NoWrap)
        self.log.setStyleSheet("font-family: Consolas, monospace; font-size: 10pt;")

        # Layout
        grid = QGridLayout(self)
        grid.addLayout(path_row, 0, 0, 1, 2)
        grid.addLayout(col_btns, 1, 0)
        grid.addWidget(self.log, 1, 1)
        grid.setColumnStretch(1, 1)

    # ---------------- helpers ----------------
    def _append(self, text: str):
        self.log.moveCursor(QTextCursor.End)
        self.log.insertPlainText(text)
        self.log.moveCursor(QTextCursor.End)

    def _root(self) -> Path:
        return Path(self.dicom_edit.text().strip()).resolve()

    def _validate(self):
        ok = self._root().is_dir()
        for b in (self.btn_extract, self.btn_preview, self.btn_annotate, self.btn_apply, self.btn_subset):
            b.setEnabled(ok)

    def _browse(self):
        path = QFileDialog.getExistingDirectory(self, "Choose DICOM root directory")
        if path:
            self.dicom_edit.setText(path)

    # ------------- run external cmds -------------
    def _run_cmd(self, cmd: List[str], tag: str):
        self._append(f"\n=== {tag} {' '.join(cmd)} ===\n")
        runner = StreamReader(cmd, cwd=REPO_ROOT)
        runner.new_line.connect(self._append)
        runner.finished.connect(lambda rc: self._append(f"=== {tag} finished (rc={rc}) ===\n"))
        runner.start()

    def _do_extract(self):
        self._run_cmd([str(PYTHON_EXE), str(EXTRACT_SCRIPT), "--dicom", str(self._root())], "EXTRACT")

    def _do_preview(self):
        self._run_cmd([str(PYTHON_EXE), str(PREVIEW_SCRIPT), "--dicom", str(self._root())], "PREVIEW")

    def _do_apply(self):
        self._run_cmd([str(PYTHON_EXE), str(APPLY_SCRIPT), str(self._root())], "APPLY")

    # ------------- subset (NEW) -------------
    def _do_subset(self):
        src_root = self._root()
        if not src_root.is_dir():
            self._append("[ERROR] Invalid DICOM root\n")
            return

        # --- destination directory ---
        dest_dir = QFileDialog.getExistingDirectory(
            self, "Choose destination directory for subset copy"
        )
        if not dest_dir:
            return  # cancelled

        # --- search terms ---
        terms, ok = QInputDialog.getText(
            self, "Protocol search terms",
            ("Enter one or more ProtocolName search terms:\n"
             "• Separate multiple terms with commas\n"
             "• Example: seq_FLAIR_acq_3D, seq_gad"),
        )
        if not ok or not terms.strip():
            return

        cmd = [
            str(PYTHON_EXE), str(SUBSET_SCRIPT),
            "-t", terms.strip(),
            str(src_root), dest_dir,
        ]
        self._run_cmd(cmd, "SUBSET")

    # ------------- flask -------------
    def _do_flask(self):
        if self._flask_proc and self._flask_proc.poll() is None:
            webbrowser.open(f"http://localhost:{FLASK_PORT}/")
            return
        env = os.environ.copy(); env.update(FLASK_APP=FLASK_APP_ENV, FLASK_ENV="development", FLASK_RUN_PORT=FLASK_PORT)
        self._flask_proc = subprocess.Popen([str(PYTHON_EXE), "-m", "flask", "run"], cwd=REPO_ROOT, env=env,
                                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        threading.Thread(target=self._pump_flask, daemon=True).start()
        webbrowser.open(f"http://localhost:{FLASK_PORT}/")

    def _pump_flask(self):
        assert self._flask_proc and self._flask_proc.stdout
        for line in self._flask_proc.stdout:
            self._append(f"[FLASK] {line}")
        rc = self._flask_proc.wait(); self._append(f"[FLASK] exited rc={rc}\n"); self._flask_proc = None

    # ------------- close -------------
    def closeEvent(self, ev):
        if self._flask_proc and self._flask_proc.poll() is None:
            self._flask_proc.terminate()
        super().closeEvent(ev)

# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = MainWindow(); win.show()
    sys.exit(app.exec_())
