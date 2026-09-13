// FineVersion clipspace source tracking — frontend extension.
//
// ComfyUI frontend >= 1.5x rewrote the MaskEditor: on save it POSTs each
// canvas layer to /upload/image with ONLY the `image` file + `type=input`
// form fields — the `original_ref` field the old editor sent is gone.
// The original filename therefore never reaches the server.
//
// This extension wraps window.fetch (the lowest-level network entry point,
// so it works regardless of how the frontend bundles its api module) and,
// at the moment of a clipspace upload, reads the source node's image widget
// (which still holds the ORIGINAL value because the editor only overwrites
// it after all uploads finish) and appends it to the FormData as
// `original_ref`.  The server-side middleware in __init__.py picks it up
// and persists it into input/clipspace/_source_map.json which
// LoadImageWithFilename_FineVersion reads to resolve the original name.
//
// NOTE: this file must live inside the directory declared by
// WEB_DIRECTORY in __init__.py (standard ComfyUI custom-node web dir).
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const CLIPSPACE_PREFIXES = [
    "clipspace-painted-masked-",
    "clipspace-painted-",
    "clipspace-paint-",
    "clipspace-mask-",
];

const UPLOAD_PATHS = [
    "/upload/image",
    "/upload/mask",
    "/api/upload/image",
    "/api/upload/mask",
];

function isClipspaceName(name) {
    return (
        typeof name === "string" &&
        CLIPSPACE_PREFIXES.some((p) => name.startsWith(p))
    );
}

// "subfolder/name [input]" -> {filename, subfolder, type}
function parseWidgetValue(value) {
    let v = String(value || "").trim();
    if (!v) return null;
    let type = "input";
    const m = v.match(/\[(input|output|temp)\]\s*$/i);
    if (m) {
        type = m[1].toLowerCase();
        v = v.slice(0, m.index).trim();
    }
    const parts = v.split("/");
    const filename = parts.pop();
    const subfolder = parts.join("/");
    if (!filename) return null;
    return { filename: filename, subfolder: subfolder, type: type };
}

// Figure out which image the MaskEditor is saving FROM.
function findSourceRef() {
    // 1) the node that opened the MaskEditor is normally the current node.
    //    At save time its image widget still holds the pre-mask value.
    try {
        const node = app.canvas && app.canvas.current_node;
        const w =
            node &&
            node.widgets &&
            node.widgets.find((w) => w.name === "image");
        if (w && w.value) {
            const ref = parseWidgetValue(w.value);
            // If the value is already a clipspace artefact (the editor may
            // overwrite it mid-save), skip it so the fallback below can find
            // the real source image instead of recording clipspace->clipspace.
            if (ref && !isClipspaceName(ref.filename)) return ref;
        }
    } catch (e) {
        /* ignore */
    }
    // 2) fallback: exactly one node in the graph has a non-clipspace image
    //    widget value — use it.
    try {
        const nodes = (app.graph && app.graph._nodes) || [];
        const candidates = [];
        for (const n of nodes) {
            const w =
                n.widgets &&
                n.widgets.find((w) => w.name === "image");
            if (w && w.value) {
                const ref = parseWidgetValue(w.value);
                if (ref && !isClipspaceName(ref.filename)) {
                    candidates.push(ref);
                }
            }
        }
        if (candidates.length === 1) return candidates[0];
    } catch (e) {
        /* ignore */
    }
    return null;
}

function requestPath(input) {
    const url =
        typeof input === "string"
            ? input
            : input && input.url
              ? input.url
              : String(input || "");
    try {
        return new URL(url, window.location.href).pathname;
    } catch (e) {
        return url;
    }
}

function injectOriginalRef(fd) {
    if (fd.get("original_ref")) return;
    const file = fd.get("image");
    const name = file && (file.name || file.filename);
    if (!isClipspaceName(name)) return;
    const ref = findSourceRef();
    if (ref) {
        fd.append("original_ref", JSON.stringify(ref));
        console.log(
            "[FineVersion] clipspace upload tracked:",
            name,
            "<-",
            ref.subfolder
                ? ref.subfolder + "/" + ref.filename
                : ref.filename
        );
    } else {
        console.warn(
            "[FineVersion] clipspace upload detected but could not determine source image"
        );
    }
}

// ---- Layer 1: wrap window.fetch (lowest level) ---------------------------
const origFetch = window.fetch.bind(window);
window.fetch = async function (input, init) {
    try {
        const path = requestPath(input);
        const method = String(
            (init && init.method) || (input && input.method) || "GET"
        ).toUpperCase();
        const fd = init && init.body;
        if (
            UPLOAD_PATHS.indexOf(path) !== -1 &&
            method === "POST" &&
            fd &&
            fd instanceof FormData
        ) {
            injectOriginalRef(fd);
        }
    } catch (e) {
        console.warn("[FineVersion] upload intercept failed:", e);
    }
    return origFetch(input, init);
};

// ---- Layer 2: wrap api.fetchApi (in case the frontend calls it through a
// captured module reference that bypasses our window.fetch patch timing) ---
try {
    const origFetchApi = api.fetchApi.bind(api);
    api.fetchApi = async function (route, options) {
        try {
            if (
                typeof route === "string" &&
                UPLOAD_PATHS.indexOf(route.split("?")[0]) !== -1 &&
                options &&
                String(options.method || "POST").toUpperCase() === "POST" &&
                options.body instanceof FormData
            ) {
                injectOriginalRef(options.body);
            }
        } catch (e) {
            console.warn("[FineVersion] fetchApi intercept failed:", e);
        }
        return origFetchApi(route, options);
    };
} catch (e) {
    console.warn("[FineVersion] could not wrap api.fetchApi:", e);
}

console.log("[FineVersion] track_clipspace extension loaded");

app.registerExtension({
    name: "FineVersion.ClipspaceSourceTracking",
});
