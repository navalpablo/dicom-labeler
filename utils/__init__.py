"""
Utility sub-package for dicom-labeler.

Re-exports the most commonly used helpers so callers can simply:
    from utils import hex_to_tag, read_header, normalize_to_uint8, ...

Nothing in here has side-effects; it only wires names.
"""
from .dicom_utils import (                 # noqa: F401
    hex_to_tag,
    read_header,
    safe_instance_number,
    sort_slices_by_instance,
    choose_slice_indices,
    gather_series_files,
)
from .image_utils import (                 # noqa: F401
    normalize_to_uint8,
    save_numpy_to_webp,
    save_dataset_slice,
)
