"""ComfyUI-LoadImageWithFilename_FineVersion custom node package.

Exposes the node classes and (when imported inside a running ComfyUI server)
registers an aiohttp middleware that records ``original_ref`` for any upload
to ``input/clipspace/``.  The middleware writes a persistent
``<input_dir>/clipspace/_source_map.json`` so the node can later resolve
``clipspace-painted-masked-xxx.png`` style widget values back to the original
filename the user painted from.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time

from aiohttp import web

from .nodes import (
    LoadImageWithFilename,
    LoadImageFolder,
    SaveImageWithFilename,
    CropImageByMask,
)
from .filename_utils import _source_map_path, CLIPSPACE_PREFIXES as _CLIPSPACE_PREFIXES  # noqa: F401

logger = logging.getLogger(__name__)

# Frontend extension directory.  ComfyUI serves everything under ./js at
# /extensions/<package-name>/ and the browser auto-imports it.  Without this
# declaration the JS files in this package are NOT published at all.
WEB_DIRECTORY = "./js"

NODE_CLASS_MAPPINGS = {
    "LoadImageWithFilename_FineVersion": LoadImageWithFilename,
    "LoadImageFolder_FineVersion": LoadImageFolder,
    "SaveImageWithFilename_FineVersion": SaveImageWithFilename,
    "CropImageByMask_FineVersion": CropImageByMask,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "LoadImageWithFilename_FineVersion": "Load Image With Filename (FineVersion)",
    "LoadImageFolder_FineVersion": "Load Image Folder (FineVersion)",
    "SaveImageWithFilename_FineVersion": "Save Image With Filename (FineVersion)",
    "CropImageByMask_FineVersion": "Crop Image By Mask (FineVersion)",
}


# ---------------------------------------------------------------------------
# aiohttp middleware: track clipspace uploads
# ---------------------------------------------------------------------------
# We capture the ``original_ref`` form field that the ComfyUI frontend sends
# alongside ``/upload/mask`` calls when the user paints / masks an image via
# the built-in MaskEditor.  The field contains the *original* filename (and
# subfolder/type) that the painted image came from, which we persist into a
# small JSON map so the node can later reverse the lookup.

_UPLOAD_PATHS = frozenset({
    "/upload/image",
    "/upload/mask",
    "/api/upload/image",
    "/api/upload/mask",
})

_MIDDLEWARE_INSTALLED = False


@web.middleware
async def _track_clipspace_sources(request, handler):
    """Wrap every upload; if it landed in ``clipspace`` remember the source."""
    # Pre-handler: when uploading a file whose name already exists in the
    # input dir, rename the OLD file with a timestamp so the incoming upload
    # keeps its clean name (instead of ComfyUI auto-adding a "(1)" suffix).
    if request.method == "POST" and request.path in _UPLOAD_PATHS:
        try:
            await _maybe_backup_old_file(request)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[FineVersion] failed to backup existing file: %s", exc)

    try:
        response = await handler(request)
    except Exception:
        # Never let our middleware mask a real handler error.
        raise

    if (
        request.method == "POST"
        and request.path in _UPLOAD_PATHS
        and getattr(response, "status", 0) == 200
    ):
        try:
            await _maybe_record_source(request, response)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[FineVersion] failed to track clipspace source: %s", exc)

    return response


async def _ensure_registered_middleware(app):
    """Install our middleware into the running aiohttp app once, eagerly.

    Called via ``PromptServer.instance.app.on_startup`` if we can hook it;
    safe to call multiple times.  Falls back to direct ``app.middlewares.append``
    for already-started apps.
    """
    global _MIDDLEWARE_INSTALLED
    if _MIDDLEWARE_INSTALLED:
        return
    try:
        app.middlewares.append(_track_clipspace_sources)
        _MIDDLEWARE_INSTALLED = True
        logger.info(
            "[FineVersion] clipspace source-tracking middleware registered"
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "[FineVersion] could not append middleware (%s); will retry on "
            "first upload request",
            exc,
        )


async def _maybe_backup_old_file(request) -> None:
    """Rename an existing same-name file before an incoming upload lands.

    ComfyUI's upload handler auto-renames the *incoming* file (``foo.png`` ->
    ``foo (1).png``) when the target already exists.  For Fine's product-image
    workflow (every product ships files named ``detail_01.png`` etc.) that
    breaks ``original_filename``.  Instead we rename the *existing* file to
    ``foo_YYYYMMDDHHMMSS.png`` so the incoming upload keeps its clean name.
    """
    post = await request.post()
    image = post.get("image")
    if image is None:
        return

    raw_name = getattr(image, "filename", None) or getattr(image, "name", None) or ""
    filename = os.path.basename(str(raw_name))
    if not filename:
        return

    upload_type = str(post.get("type") or "input").lower()
    if upload_type != "input":
        return

    subfolder = post.get("subfolder", "") or ""

    # Lazy import so importing this module never breaks on systems where
    # ComfyUI is not on sys.path.
    import folder_paths

    input_dir = folder_paths.get_input_directory()
    target_dir = input_dir
    if subfolder:
        norm = os.path.normpath(str(subfolder))
        # Refuse path traversal / absolute paths.
        if norm.startswith("..") or os.path.isabs(norm):
            return
        target_dir = os.path.join(input_dir, norm)

    target_path = os.path.abspath(os.path.join(target_dir, filename))
    if not os.path.isfile(target_path):
        return

    timestamp = time.strftime("%Y%m%d%H%M%S")
    stem, ext = os.path.splitext(filename)
    new_name = f"{stem}_{timestamp}{ext}"
    new_path = os.path.join(target_dir, new_name)
    counter = 1
    while os.path.exists(new_path):
        new_name = f"{stem}_{timestamp}_{counter}{ext}"
        new_path = os.path.join(target_dir, new_name)
        counter += 1

    try:
        os.rename(target_path, new_path)
        logger.info("[FineVersion] renamed existing file: %s -> %s", filename, new_name)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[FineVersion] rename failed: %s", exc)


async def _maybe_record_source(request, response) -> None:
    """Extract ``original_ref`` + the server-side filename and persist.

    Works with BOTH the legacy and the modern (>= 1.5x) MaskEditor:

    - Legacy editor POSTs to /upload/mask with ``original_ref`` AND
      ``subfolder=clipspace`` form fields.
    - Modern editor (rewritten in Vue) POSTs to /upload/image with only the
      ``image`` file + ``type=input``; no original_ref.  Our frontend
      extension ``track_clipspace.js`` wraps api.fetchApi and re-injects the
      ``original_ref`` field at upload time, so it arrives here again.

    The clipspace destination is determined from the RESPONSE (where the file
    actually landed), falling back to the request's subfolder field.
    """
    post = await request.post()
    original_ref_raw = post.get("original_ref")
    if not original_ref_raw:
        return

    try:
        original_ref = json.loads(original_ref_raw)
    except (TypeError, json.JSONDecodeError):
        return

    try:
        body_bytes = response.body
        if isinstance(body_bytes, (bytes, bytearray)):
            resp_data = json.loads(body_bytes.decode("utf-8"))
        else:
            resp_data = json.loads(body_bytes)
    except Exception:
        return

    if not isinstance(resp_data, dict):
        return

    actual_filename = resp_data.get("name")
    if not actual_filename:
        return

    resp_subfolder = resp_data.get("subfolder", "") or ""
    req_subfolder = post.get("subfolder", "") or ""
    # Modern MaskEditor uploads to /upload/image with NO subfolder field —
    # the file lands in the input root with a ``clipspace-*`` basename. So we
    # must also treat a clipspace-prefixed basename as a clipspace upload,
    # otherwise we wrongly skip recording and ``original_filename`` degrades
    # to a bare timestamp.
    actual_basename = os.path.basename(actual_filename)
    is_clipspace = (
        resp_subfolder == "clipspace"
        or req_subfolder == "clipspace"
        or any(actual_basename.startswith(p) for p in _CLIPSPACE_PREFIXES)
    )
    if not is_clipspace:
        return

    # Lazy import so importing this module never breaks the package on systems
    # where ComfyUI is not on sys.path (e.g. running our unit tests directly).
    import folder_paths

    input_dir = folder_paths.get_input_directory()
    clipspace_dir = os.path.join(input_dir, "clipspace")
    os.makedirs(clipspace_dir, exist_ok=True)

    source_map_path = _source_map_path()
    source_map: dict = {}
    if os.path.exists(source_map_path):
        try:
            with open(source_map_path, "r", encoding="utf-8") as fh:
                loaded = json.load(fh)
                if isinstance(loaded, dict):
                    source_map = loaded
        except Exception:
            source_map = {}

    original_filename = str(original_ref.get("filename", "") or "")
    # Mask-on-mask chains: if the "original" is itself a clipspace artefact
    # (user re-edited an already-masked image), follow the existing map to
    # the root original file.
    chain_depth = 0
    while (
        original_filename
        and any(original_filename.startswith(p) for p in _CLIPSPACE_PREFIXES)
        and chain_depth < 10
    ):
        entry = source_map.get(original_filename)
        if not isinstance(entry, dict):
            break
        nxt = entry.get("filename", "")
        if not nxt or nxt == original_filename:
            break
        original_filename = nxt
        chain_depth += 1

    source_map[os.path.basename(actual_filename)] = {
        "filename": original_filename,
        "subfolder": original_ref.get("subfolder", "") or "",
        "type": original_ref.get("type", "input") or "input",
    }

    tmp_path = source_map_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(source_map, fh, ensure_ascii=False, indent=2)
    os.replace(tmp_path, source_map_path)


def register_middleware() -> None:
    """Attach the clipspace-tracking middleware to the running ComfyUI server.

    Safe to call multiple times — only the first call has any effect.
    Fails silently if ComfyUI's ``PromptServer`` is not importable yet (e.g.
    the package is being imported outside of a server context).
    """
    global _MIDDLEWARE_INSTALLED
    if _MIDDLEWARE_INSTALLED:
        return
    try:
        from server import PromptServer  # type: ignore
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[FineVersion] could not import server.PromptServer; "
            "clipspace tracking disabled (%s)",
            exc,
        )
        return

    try:
        app = PromptServer.instance.app
    except Exception as exc:  # noqa: BLE001
        logger.debug("[FineVersion] PromptServer.instance not ready yet: %s", exc)
        return

    # Hook on_startup so the middleware is installed the moment the app
    # starts handling requests — survives the early-import race condition.
    try:
        app.on_startup.append(lambda _app: _ensure_registered_middleware(_app))
    except Exception as exc:  # noqa: BLE001
        logger.debug("[FineVersion] could not append on_startup hook: %s", exc)

    # Also try to install right now in case the app is already running.
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Loop already running — the on_startup hook above will fire.
            return
    except RuntimeError:
        pass

    try:
        _ensure_registered_middleware(app)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[FineVersion] synchronous install failed (%s); relying on on_startup", exc)


# Eagerly try to register the middleware as soon as the package is imported by
# ComfyUI's node loader.  If the server isn't ready yet (very early startup),
# the failure is swallowed and ``register_middleware`` can be called again
# later from any code path that runs after ``PromptServer`` is initialised.
try:
    register_middleware()
except Exception:  # noqa: BLE001
    pass


__all__ = [
    "NODE_CLASS_MAPPINGS",
    "NODE_DISPLAY_NAME_MAPPINGS",
    "register_middleware",
]