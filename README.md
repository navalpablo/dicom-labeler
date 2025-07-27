# DICOM Series Labeler

Lightweight, local‑only **4‑step pipeline** for browsing, annotating and finally
**writing labels back into DICOM headers** – now with a PyQt5 GUI and safe
2D/3D/4D prefix support.

> **Important**  
> Before doing anything, sort your raw DICOMs with  
> [`dicom_sorting_toolkit` v0.1.5](https://github.com/navalpablo/dicom_sorting_toolkit)  
> so that every *SeriesInstanceUID* gets its own folder.  
> All subsequent scripts assume this structure.

---

## Installation

```bash
conda create -n dicom-labeler python=3.10            # or use mamba
conda activate dicom-labeler

# core deps
pip install pydicom tqdm pillow numpy flask pyqt5

# (optional) enable JPEG‑LS / JPEG‑2000 compressed PixelData
conda install -c conda-forge pylibjpeg pylibjpeg-libjpeg pylibjpeg-openjpeg
```

---

## Quick‑start (terminal)

```bash
# 0 · sort your studies first
       # use release dicom sorting toolkit v0.1.5

cd dicom-labeler

# 1 · extract one row per SeriesInstanceUID  →  series_info.tsv
python extract_dicom_headers.py --dicom /sorted/DICOMs

# 2 · generate 8‑slice WebP thumbnails per series  →  previews/
python generate_previews.py --dicom /sorted/DICOMs

# 3 · launch browser‑based annotator (Flask) on port 5000
export FLASK_APP=label_server.app:create_flask_app
flask run
# open http://localhost:5000/ and assign labels

# 4 · embed labels back into every DICOM or move DELETEd series
python apply_labels.py /sorted/DICOMs -j 12
```

---

## One‑click GUI

```bash
python dicom_labeler_gui.py
```

The PyQt5 window guides you through all four steps and streams live logs.

---

## Pipeline details

| Step | Script | Output | Notes |
|------|--------|--------|-------|
| **1** | `extract_dicom_headers.py` | `series_info.tsv` | Fast header sweep, preserves existing Annotation column, infers plane orientation |
| **2** | `generate_previews.py` | `previews/UID_slice*.webp` + `UID.json` | 8 evenly‑spaced slices, intensity‑windowed |
| **3** | Flask UI (`label_server/`, or GUI) | modifies `series_info.tsv`, appends to `labels_config.txt` | Groups series by StudyUID; dropdown remembers custom labels |
| **4** | `apply_labels.py` | edits DICOM files in‑place (atomic) or moves them under `WAITING_DELETION/` | Adds prefix `seq_{annotation}_acq_{2D/3D/4D}_plane_{plane}___` and keeps total length ≤ 64 bytes |

### 2D / 3D / 4D rule

* **4D** if *NumberOfTemporalPositions > 1*  
* **3D** if *SliceThickness < 2 mm* **or** *SpacingBetweenSlices < 2 mm*  
* **2D** otherwise (missing tags are ignored)

`apply_labels.py` writes to a **temporary file** first, then atomically
replaces the original to avoid corruption when deferred PixelData are used.

---

## Repository layout

```
dicom-labeler/
├── dicom_labeler_gui.py       # optional PyQt5 front‑end
├── extract_dicom_headers.py   # step 1
├── generate_previews.py       # step 2
├── apply_labels.py            # step 4 (safe, adds 2D/3D/4D prefix)
├── label_server/              # Flask app (step 3)
│   ├── app.py
│   ├── templates/
│   └── static/
├── series_info.tsv            # generated – git‑ignore
├── previews/                  # generated – git‑ignore
├── labels_config.txt          # label list – auto‑grows
└── README.md
```

---

## Tips

* **Dry‑run** `apply_labels.py /path -n` to preview edits without touching
  files (summary only).
* **Logs** `apply_labels.py … -l actions.tsv` records every move / edit.
* **Overwrite previews** `generate_previews.py … --overwrite`.
* **Large datasets** Use `extract_dicom_headers.py --read_all` if your
  series folders contain mixed sequences.
* The GUI keeps a Flask server alive between clicks; reopen the browser
  to continue labeling later.

---

## License

MIT – free for academic and commercial use.  No warranties.
