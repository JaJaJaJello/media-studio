# Media Studio

A fast, dark-themed viewer/editor for images, GIFs, videos and audio.

**Run:** `dist\MediaStudio\MediaStudio.exe` (keep the `_internal` folder next to it).
**Rebuild:** `build.bat`  ·  **Run from source:** `.venv\Scripts\python.exe run.py [file]`

## Open with / default app
In the app: **⋯ menu → Add to Windows ‘Open with’ menu**. Then right-click any image/GIF/video →
*Open with → Choose another app → Media Studio → Always*, or use *Settings → Apps → Default apps → Media Studio*.
(Per-user registry keys only; **⋯ → Remove from Windows ‘Open with’** undoes it. If you move the exe, register again.)

## Images
Scroll = zoom at cursor, drag / middle-drag / Space+drag = pan, double-click = fit ↔ 100%, ← / → = next file in folder.
Tools: **V** pan · **C** crop (aspect presets, Enter applies) · **P** pen · **H** highlighter · **A** arrow · **L** line ·
**R** rectangle · **E** ellipse · **T** text · **B** pixelate/redact · **X** eraser. Shift constrains shapes.
Ctrl+R rotate, flip menu, Ctrl+Z/Y undo/redo, Ctrl+C copy, Ctrl+S save a copy, Ctrl+Shift+S overwrite.

## Video / GIF / audio
Space play/pause · ← / → one frame · Shift+← / → one second · J / L ±5 s · Home / End.
Timeline: click/drag to scrub · wheel zooms (down to individual frames) · Shift+wheel scrolls ·
drag a segment edge to trim it.
**S** split at playhead · **Delete** remove segment · **Q** cut everything before playhead · **W** cut everything after.
Removed ranges stay visible (hatched) and are skipped on playback. **C** draws a crop box applied on export.
Camera button: copy / save the current frame, or open it in a new window to annotate.
**Ctrl+E** export → MP4 (H.264), WebM (VP9), GIF (palette-optimised), MP3 or WAV, with size/fps options.
