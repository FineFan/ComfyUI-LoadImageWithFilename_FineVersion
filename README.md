# ComfyUI LoadImageWithFilename (FineVersion)

Fork of [kymeraj/comfyui-load-image-with-filename](https://github.com/kymeraj/comfyui-load-image-with-filename).
Renamed so it can sit alongside the upstream node in the same ComfyUI
install without colliding.

> **Naming**: every node class key and display name carries the
> ``_FineVersion`` / ``(FineVersion)`` suffix so the upstream package can be
> installed side-by-side.  All log messages are prefixed with
> ``[FineVersion]`` for the same reason.

This custom node extends ComfyUI's image loading functionality with filename output and folder loading capabilities.

## Features

### LoadImageWithFilename
- **Enhanced Load Image Node**: Based on the original ComfyUI LoadImage node
- **Filename Output**: Returns the filename of the loaded image as a STRING output
- **Compatible**: Maintains all original functionality (IMAGE and MASK outputs)

### LoadImageFolder
- **Folder Loading**: Load all images from a specified folder path
- **Batch Processing**: Returns all images concatenated as a single tensor
- **Filename Tracking**: Returns a list of all loaded filenames
- **Error Handling**: Gracefully handles corrupted or unsupported image files

### SaveImageWithFilename
- **Enhanced Save Image Node**: Based on the original ComfyUI SaveImage node
- **Custom Filenames**: Accepts single filenames or comma-separated lists of filenames
- **Flexible Naming**: Can use provided filenames or fall back to default naming
- **Batch Support**: Handles multiple images with corresponding filenames

## Installation

1. Copy the whole `ComfyUI-LoadImageWithFilename_FineVersion` folder into your
   ComfyUI `custom_nodes` directory
2. Restart ComfyUI
3. The new nodes will appear in the "image" category
4. **Hard-refresh the browser (Ctrl+F5)** so the frontend extension
   `js/track_clipspace.js` is picked up (required for `original_filename` to
   survive the MaskEditor; see below)

## Usage

### LoadImageWithFilename
- **Input**: Select an image file from the dropdown
- **Outputs**:
  - `image`: The loaded image tensor
  - `mask`: The image mask (if available)
  - `filename`: The filename of the loaded image
  - `original_filename`: The ORIGINAL filename, even after painting a mask in
    the MaskEditor (which renames the widget value to
    `clipspace-painted-masked-xxx.png`)

### How `original_filename` works
The modern ComfyUI frontend (>= 1.5x) MaskEditor no longer sends the
original image reference to the server when saving a painted mask. This
package therefore ships two cooperating pieces:

- `js/track_clipspace.js` (frontend extension, served via
  `WEB_DIRECTORY = "./js"`): wraps `window.fetch` and appends an
  `original_ref` field to clipspace uploads, read from the node's image
  widget before the editor overwrites it.
- `__init__.py` (server middleware): intercepts `/upload/image` +
  `/upload/mask`, persists the mapping into
  `input/clipspace/_source_map.json`, and resolves chains
  (mask-on-mask) back to the root original file.

### LoadImageFolder
- **Input**: Enter a folder path as a string
- **Outputs**:
  - `image`: All images from the folder concatenated as a single tensor
  - `mask`: All masks concatenated as a single tensor
  - `filenames`: List of all loaded filenames

### SaveImageWithFilename
- **Inputs**:
  - `images`: The images to save (IMAGE tensor)
  - `filenames`: Single filename or comma-separated list of filenames (optional)
  - `filename_prefix`: Prefix for default naming (optional)
- **Behavior**:
  - If filenames are provided, uses them for the corresponding images
  - If no filenames or fewer filenames than images, uses default naming for remaining images
  - Automatically adds .png extension to filenames
  - Saves to ComfyUI output directory

## Requirements

- ComfyUI
- PIL (Pillow)
- PyTorch
- NumPy

## Notes

- The LoadImageFolder node will skip any non-image files in the selected folder
- If no valid images are found in a folder, empty tensors will be returned
- All images in a folder must have the same dimensions for proper concatenation
- Error messages are printed to console for any files that fail to load
- The SaveImageWithFilename node preserves original filenames when possible
- If filenames contain extensions, they will be replaced with .png

## Based On

- [ComfyUI LoadImage Node](https://github.com/comfyanonymous/ComfyUI/blob/master/nodes.py)
- [ComfyUI SaveImage Node](https://github.com/comfyanonymous/ComfyUI/blob/master/nodes.py)
- [Issue #8699](https://github.com/comfyanonymous/ComfyUI/issues/8699) - Request for filename output functionality