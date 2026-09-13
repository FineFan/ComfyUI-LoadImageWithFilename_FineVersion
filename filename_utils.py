"""Filename helper utilities shared by LoadImageWithFilename nodes.

Centralises every filename-related utility so we don't duplicate logic across
classes and so the rules are easy to test / change in one place.

Clipspace naming convention used by ComfyUI frontend
----------------------------------------------------
When the user paints / masks an image in the MaskEditor, the frontend uploads
the result into ``input/clipspace/`` under one of the following names::

    clipspace-mask-<timestamp>.png
    clipspace-paint-<timestamp>.png
    clipspace-painted-<timestamp>.png
    clipspace-painted-masked-<timestamp>.png

The LoadImage widget value then becomes::

    clipspace/clipspace-painted-masked-<timestamp>.png [input]

There is **no inherent** way for a downstream node to know which original file
the clipspace artefact came from.  To recover it, the upload-mask route in
``server.py`` already accepts an ``original_ref`` field that names the original
asset; we persist that mapping into ``<input_dir>/clipspace/_source_map.json``
via a server middleware (see ``__init__.py``) and look it up here.
"""
from __future__ import annotations

import os
import json
import threading

import folder_paths


# clipspace filename prefixes, longest first so prefix-matching is unambiguous.
CLIPSPACE_PREFIXES = (
    "clipspace-painted-masked-",
    "clipspace-painted-",
    "clipspace-paint-",
    "clipspace-mask-",
)


# Guard _source_map.json reads/writes from concurrent server + node threads.
_SOURCE_MAP_LOCK = threading.Lock()


def _clipspace_dir() -> str:
    """Absolute path to <input_dir>/clipspace."""
    return os.path.join(folder_paths.get_input_directory(), "clipspace")


def _source_map_path() -> str:
    """Absolute path to the persistent source map JSON."""
    return os.path.join(_clipspace_dir(), "_source_map.json")


def load_source_map() -> dict:
    """Load the source map JSON; return ``{}`` on any error / missing file."""
    path = _source_map_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
            if isinstance(data, dict):
                return data
    except Exception as exc:  # noqa: BLE001
        print(f"[LoadImageWithFilename_FineVersion] failed to read _source_map.json: {exc}")
    return {}


def save_source_map(source_map: dict) -> None:
    """Persist the source map JSON; create parent dir if needed."""
    if not isinstance(source_map, dict):
        return
    try:
        os.makedirs(_clipspace_dir(), exist_ok=True)
        with _SOURCE_MAP_LOCK:
            tmp = _source_map_path() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(source_map, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, _source_map_path())
    except Exception as exc:  # noqa: BLE001
        print(f"[LoadImageWithFilename_FineVersion] failed to save _source_map.json: {exc}")


def register_source(clipspace_filename: str, original_filename: str,
                    original_subfolder: str = "", original_type: str = "input") -> None:
    """Add / overwrite one entry in the source map."""
    if not clipspace_filename or not original_filename:
        return
    clipspace_basename = os.path.basename(clipspace_filename)
    original_basename = os.path.basename(original_filename)
    with _SOURCE_MAP_LOCK:
        mapping = load_source_map()
        mapping[clipspace_basename] = {
            "filename": original_basename,
            "subfolder": original_subfolder or "",
            "type": original_type or "input",
        }
        save_source_map(mapping)


def _is_clipspace_basename(basename: str) -> bool:
    return any(basename.startswith(p) for p in CLIPSPACE_PREFIXES)


def resolve_original_filename(widget_value: str, fallback_basename: str | None = None) -> str:
    """Return the **original** filename for a widget value.

    - If the widget does **not** point at a clipspace artefact, the basename of
      the value (with the ``[input]`` annotation stripped) is returned as-is.
    - If it **does** point at a clipspace artefact, we look up the source map.
      On a hit we return the original filename (with subfolder if any); on a
      miss we fall back to the basename with the clipspace prefix stripped so
      the user at least gets something more useful than a raw timestamp.
    """
    if widget_value is None:
        return ""

    raw = str(widget_value).strip()
    if not raw:
        return raw

    # Strip the ComfyUI "[input]" / "[output]" / "[temp]" annotation and any
    # leading "clipspace/" subfolder prefix so we only deal with the basename.
    for suffix in (" [input]", "[input]", " [output]", "[output]", " [temp]", "[temp]"):
        if raw.endswith(suffix):
            raw = raw[: -len(suffix)].rstrip()
            break

    basename = os.path.basename(raw)
    if not basename:
        return ""

    if not _is_clipspace_basename(basename):
        # Plain image — return its basename exactly as ComfyUI sees it.
        return basename

    # Clipspace file — try the persistent source map.
    mapping = load_source_map()
    entry = mapping.get(basename)
    if isinstance(entry, dict):
        original = entry.get("filename")
        subfolder = entry.get("subfolder") or ""
        if original:
            return f"{subfolder}/{original}" if subfolder else original

    # Fallback: drop the clipspace prefix so the user sees the timestamp only.
    for prefix in CLIPSPACE_PREFIXES:
        if basename.startswith(prefix):
            return basename[len(prefix):]

    if fallback_basename is not None:
        return fallback_basename
    return basename