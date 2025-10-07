#!/usr/bin/env python3
"""
Trim uniform borders from images (with alpha-aware cropping) and add a 5% border.

Usage:
  python trim_uniform_border.py /path/to/image_or_directory

Behavior:
- If the image has an alpha channel that is NOT fully opaque (i.e., at least one pixel alpha != 255),
  we crop to the nontransparent content (alpha > 0) and add a 5% TRANSPARENT border.
- Otherwise, we sample the 4 corners to detect a uniform background color.
  If all corners match within a threshold of 4 (per-channel), we crop that background away,
  then add a 5% border filled with that background color.
- If corners don't agree, we do nothing.
- Writes back in-place.

Supported inputs: anything Pillow can open. If a single file is unsupported, an exception is raised.
If a directory is provided, incompatible files are skipped.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Tuple, Optional, List

import sys
import math

from PIL import Image, ImageOps, UnidentifiedImageError
import numpy as np

LOSSY_THRESHOLD = 4  # per-channel tolerance for lossy compressed backgrounds
ALPHA_NONZERO_THRESHOLD = 0  # consider alpha > 0 as foreground
BORDER_FRACTION = 0.05  # 5% border after cropping

# Common browser-friendly raster formats (we still rely on Pillow to actually open them)
COMPAT_EXTS = {
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff",
}

def _corner_pixels_rgb(im_rgb: Image.Image) -> List[Tuple[int, int, int]]:
    w, h = im_rgb.size
    # Guard tiny images
    if w == 0 or h == 0:
        return []
    pts = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]
    return [im_rgb.getpixel(p) for p in pts]

def _colors_similar(c1: Tuple[int, int, int], c2: Tuple[int, int, int], thr: int) -> bool:
    return (abs(c1[0] - c2[0]) <= thr and
            abs(c1[1] - c2[1]) <= thr and
            abs(c1[2] - c2[2]) <= thr)

def _background_from_corners(corners: List[Tuple[int, int, int]], thr: int) -> Optional[Tuple[int, int, int]]:
    """
    Returns a background color if all corners agree within threshold; otherwise None.
    We simply require all corners to be similar to the first.
    """
    if not corners:
        return None
    ref = corners[0]
    for c in corners[1:]:
        if not _colors_similar(ref, c, thr):
            return None
    return ref  # use the first; all are within threshold

def _crop_to_alpha(im_rgba: Image.Image) -> Optional[Image.Image]:
    """
    Crop RGBA image to region where alpha > ALPHA_NONZERO_THRESHOLD.
    Returns a cropped image or None if fully transparent (or cannot crop).
    """
    alpha = np.array(im_rgba.getchannel("A"))
    mask = alpha > ALPHA_NONZERO_THRESHOLD
    if not mask.any():
        return None  # fully transparent; nothing to show
    ys = np.where(mask.any(axis=1))[0]
    xs = np.where(mask.any(axis=0))[0]
    top, bottom = int(ys.min()), int(ys.max())
    left, right = int(xs.min()), int(xs.max())
    # PIL crop box is (left, upper, right_exclusive, lower_exclusive)
    return im_rgba.crop((left, top, right + 1, bottom + 1))

def _crop_to_color_background(im_rgb: Image.Image, bg: Tuple[int, int, int], thr: int) -> Optional[Image.Image]:
    """
    Crop RGB image to the bounding box of pixels that differ from bg (beyond threshold).
    Returns cropped image or None if the entire image is background / no foreground detected.
    """
    arr = np.array(im_rgb).astype(np.int16)  # prevent overflow on subtraction
    bg_arr = np.array(bg, dtype=np.int16).reshape((1, 1, 3))
    diff = np.abs(arr - bg_arr)
    # A pixel is "foreground" if ANY channel differs by more than thr
    fg_mask = (diff > thr).any(axis=2)

    if not fg_mask.any():
        return None  # no foreground; the whole image is background color

    ys = np.where(fg_mask.any(axis=1))[0]
    xs = np.where(fg_mask.any(axis=0))[0]
    top, bottom = int(ys.min()), int(ys.max())
    left, right = int(xs.min()), int(xs.max())
    return im_rgb.crop((left, top, right + 1, bottom + 1))

def _add_border(im: Image.Image, fraction: float, fill) -> Image.Image:
    """
    Add a percentage border around the image. 'fill' can be an RGB tuple or an RGBA tuple.
    """
    w, h = im.size
    bw = max(1, int(math.ceil(w * fraction)))
    bh = max(1, int(math.ceil(h * fraction)))
    return ImageOps.expand(im, border=(bw, bh, bw, bh), fill=fill)

def trim_img_border_inplace(path: Path) -> bool:
    """
    Process a single image file in place.
    Returns True if modified & saved, False if left unchanged.
    Raises ValueError if the file is not a supported image.
    """
    try:
        with Image.open(path) as im:
            # Load to avoid lazy file handles if we convert later
            im.load()
            orig_format = im.format  # e.g., 'PNG', 'JPEG', etc.
            # Normalize modes
            has_alpha_channel = ("A" in im.getbands())
            if has_alpha_channel:
                # Determine if alpha is fully opaque
                alpha = np.array(im.getchannel("A"))
                alpha_is_all_opaque = (alpha == 255).all()
            else:
                alpha_is_all_opaque = True

            if has_alpha_channel and not alpha_is_all_opaque:
                # Alpha-based cropping path
                im_rgba = im.convert("RGBA")
                cropped = _crop_to_alpha(im_rgba)
                if cropped is None:
                    # Nothing to crop; either fully transparent or empty
                    return False

                # Add 5% transparent border
                border_fill = (0, 0, 0, 0)
                final = _add_border(cropped, BORDER_FRACTION, border_fill)

                # Save back. Keep alpha-friendly format; original format should already support alpha.
                # If it somehow doesn't, fallback to PNG.
                save_kwargs = {}
                fmt = orig_format if orig_format in {"PNG", "WEBP", "TIFF"} else "PNG"
                final.save(path, format=fmt, **save_kwargs)
                return True

            else:
                # Color background detection path (no alpha or fully opaque alpha)
                im_rgb = im.convert("RGB")
                corners = _corner_pixels_rgb(im_rgb)
                bg = _background_from_corners(corners, LOSSY_THRESHOLD)
                if bg is None:
                    # Corners disagree: do nothing
                    return False

                cropped = _crop_to_color_background(im_rgb, bg, LOSSY_THRESHOLD)
                if cropped is None:
                    # Entire image is background color; nothing to crop
                    return False

                # Add 5% border with the background color
                final = _add_border(cropped, BORDER_FRACTION, bg)

                # Save back in-place. If original was JPEG, keep JPEG; else let Pillow infer by extension.
                save_kwargs = {}
                # Preserve original format when possible
                if orig_format:
                    final = final.convert("RGB")  # ensure no accidental alpha when saving JPEG
                    final.save(path, format=orig_format, **save_kwargs)
                else:
                    final.save(path, **save_kwargs)
                return True

    except UnidentifiedImageError as e:
        raise ValueError(f"Unsupported or unreadable image format: {path}") from e

def iter_directory(dir_path: Path) -> None:
    """
    Iterate over compatible files in a directory (non-recursive),
    processing those Pillow can open; skip others gracefully.
    """
    processed = 0
    changed = 0
    skipped = 0

    for p in sorted(dir_path.iterdir()):
        if not p.is_file():
            continue
        if p.suffix.lower() not in COMPAT_EXTS:
            # Try anyway—maybe Pillow supports it—but we won't spam errors if it doesn't.
            try_ext = True
        else:
            try_ext = True

        if not try_ext:
            skipped += 1
            continue

        try:
            modified = trim_img_border_inplace(p)
            processed += 1
            if modified:
                changed += 1
                print(f"✅ Modified: {p}")
            else:
                print(f"➖ Unchanged: {p}")
        except ValueError:
            skipped += 1
            print(f"⏭️ Skipped (unsupported): {p}")
        except Exception as ex:
            skipped += 1
            print(f"⚠️ Error on {p}: {ex}")

    print(f"\nDone. Processed {processed} file(s); changed {changed}; skipped {skipped}.")

def main():
    parser = argparse.ArgumentParser(
        description="Trim uniform borders from images (alpha-aware) and add a 5% border, in place."
    )
    parser.add_argument("path", help="Path to an image file or a directory")
    args = parser.parse_args()

    target = Path(args.path)
    if not target.exists():
        print(f"Error: path does not exist: {target}", file=sys.stderr)
        sys.exit(1)

    if target.is_dir():
        iter_directory(target)
    else:
        try:
            modified = trim_img_border_inplace(target)
            if modified:
                print(f"✅ Modified: {target}")
            else:
                print(f"➖ Unchanged: {target}")
        except ValueError as ve:
            # For single-file case, raise per spec
            print(f"❌ {ve}", file=sys.stderr)
            sys.exit(2)

if __name__ == "__main__":
    main()
