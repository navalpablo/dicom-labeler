# DICOM Series Labeler

Lightweight, local‑only toolkit for browsing and annotating neuro­imaging
DICOM series (T1w, FLAIR, etc.).  
You point it at a directory full of studies, it extracts metadata,
generates 8‑slice thumbnails per series, and spins up a small Flask UI
where you scroll through batches of 20 series and assign labels.  
Annotations are written back to **series_info.tsv**; new labels are
appended to **labels_config.txt**.

---

## Quick start

```bash
# 1 · create & activate conda env
conda env create -f environment.yml        
conda activate dicom-labeler

# 2 · extract one row per series
python extract_dicom_headers.py \
    --dicom /path/to/DICOM_root \
    --output series_info.tsv

# 3 · generate 8‑slice WebP previews
python generate_previews.py \
    --dicom /path/to/DICOM_root \
    --manifest series_info.tsv \
    --outdir previews

# 4 · launch labeling UI (bash)
export FLASK_APP=label_server.app
flask run                                   # open http://localhost:5000/

# 4 · launch labeling UI (cmd)
set FLASK_APP=label_server.app:create_flask_app
flask run                                  # open http://localhost:5000/


```



---

## Repository layout

```
dicom-labeler/
├── extract_dicom_headers.py   # step 1
├── generate_previews.py       # step 2
├── label_server/              # step 3–4 (Flask app)
│   ├── __init__.py
│   ├── app.py
│   └── templates/
│       ├── base.html
│       └── page.html
├── utils/
│   ├── dicom_utils.py
│   └── image_utils.py
├── labels_config.txt          # label list (append‑only)
├── series_info.tsv            # generated manifest (+Annotation col)
├── previews/                  # generated WebP thumbnails
├── environment.yml            # conda env spec
└── README.md
```

*Generated files (`series_info.tsv`, `previews/`) should be **git‑ignored**.*

---

## How it works

1. **Extract metadata**  
   `extract_dicom_headers.py` walks the directory, reads one DICOM header
   per series (fast), and builds `series_info.tsv`.  
   *Re‑run any time; existing Annotation values are preserved.*

2. **Generate previews**  
   `generate_previews.py` sorts slices by *InstanceNumber*, picks 8 evenly
   spaced indices, rescales intensities (1–99 % window), and saves
   `<SeriesUID>_slice0.webp … slice7.webp` under `previews/`.

3. **Annotate in browser**  
   `flask run` serves `/page/1` (20 series).  
   * Pick an existing label from the dropdown **or** type a new one.  
   * Click **Save Annotations** → TSV updated, new labels appended to
     `labels_config.txt`, page auto‑advances.  
   * Completed series are tinted green; header shows progress bar.

---

## Tips & tricks

* **Add labels later:** edit `labels_config.txt` (one per line) or just
  type new values in the UI—​the file is appended automatically.
* **Overwrite thumbnails:**  
  `python generate_previews.py … --overwrite`
* **Large datasets:** `extract_dicom_headers.py` reads only the first 5
  DICOMs per folder; use `--read_all` for exhaustive scans.
* **Compressed PixelData:** enable JPEG‑LS / JPEG‑2000 via  
  `conda install pylibjpeg pylibjpeg-libjpeg pylibjpeg-openjpeg`.

---

## Testing

```bash
pytest -q
```

---

## License

MIT – free for academic and commercial use.  No warranties.
