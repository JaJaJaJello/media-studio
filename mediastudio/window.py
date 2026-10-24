"""Main window: top bar, tool rail, canvas, transport + timeline, status bar."""
import ctypes
import os
import re
import sys
import time
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QImageReader, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QButtonGroup, QColorDialog, QComboBox, QFileDialog, QFrame,
                               QHBoxLayout, QLabel, QMainWindow, QMenu, QMessageBox, QSizePolicy, QSlider,
                               QToolButton, QVBoxLayout, QWidget)

from . import assoc, theme
from .canvas import Canvas
from .timeline import Timeline, fmt_time

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".jfif", ".bmp", ".gif", ".webp", ".tif", ".tiff", ".ico", ".tga",
              ".ppm", ".pgm", ".pbm", ".avif", ".apng", ".svg"}
VIDEO_EXTS = {".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".wmv", ".flv", ".mpg", ".mpeg", ".ts",
              ".mts", ".m2ts", ".3gp", ".ogv", ".vob"}
AUDIO_EXTS = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma"}
ANIM_EXTS = {".gif", ".webp", ".png", ".apng"}
ALL_EXTS = IMAGE_EXTS | VIDEO_EXTS | AUDIO_EXTS
SAVE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}

SWATCHES = ["#ff4d5e", "#ff9f43", "#ffd43b", "#3ddc84", "#38d1b0", "#5b8cff", "#a66bff", "#ffffff", "#111111"]
TOOLS = [("move", "Pan & zoom (V)", "V"), ("crop", "Crop (C)", "C"), None,
         ("pen", "Pen (P)", "P"), ("highlight", "Highlighter (H)", "H"), ("arrow", "Arrow (A)", "A"),
         ("line", "Line (L)", "L"), ("rect", "Rectangle (R)", "R"), ("ellipse", "Ellipse (E)", "E"),
         ("text", "Text (T)", "T"), ("redact", "Pixelate / redact (B)", "B"), ("eraser", "Eraser (X)", "X")]
ASPECTS = [("Free", None), ("Original", "orig"), ("Square 1:1", 1.0), ("4:3", 4 / 3), ("3:2", 3 / 2),
           ("16:9", 16 / 9), ("9:16", 9 / 16), ("3:4", 3 / 4)]

_windows = []


def natural_key(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def load_image(path):
    """Returns (QImage, None) for stills or (None, PIL image) for animations."""
    ext = Path(path).suffix.lower()
    if ext in ANIM_EXTS:
        try:
            from PIL import Image
            im = Image.open(path)
            if getattr(im, "is_animated", False) and getattr(im, "n_frames", 1) > 1:
                return None, im
        except Exception:
            pass
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    img = reader.read()
    if not img.isNull():
        return img, None
    from PIL import Image, ImageOps
    im = ImageOps.exif_transpose(Image.open(path)).convert("RGBA")
    q = QImage(im.tobytes(), im.width, im.height, im.width * 4, QImage.Format_RGBA8888).copy()
    return q, None


def tool_button(name, tip, checkable=False, size=18, color=theme.TEXT):
    b = QToolButton()
    b.setIcon(theme.icon(name, size, color))
    b.setIconSize(QSize(size, size))
    b.setToolTip(tip)
    b.setCheckable(checkable)
    b.setFocusPolicy(Qt.NoFocus)
    b.setAutoRaise(True)
    b.setCursor(Qt.PointingHandCursor)
    return b


def separator(vertical=True):
    f = QFrame()
    f.setObjectName("Separator")
    if vertical:
        f.setFixedSize(1, 22)
    else:
        f.setFixedSize(22, 1)
    return f


class Toast(QLabel):
    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("Toast")
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.hide()
        self._t = QTimer(self, singleShot=True)
        self._t.timeout.connect(self.hide)

    def show_msg(self, text, ms=2200):
        self.setText(text)
        self.adjustSize()
        p = self.parentWidget()
        self.move((p.width() - self.width()) // 2, p.height() - self.height() - 24)
        self.raise_()
        self.show()
        self._t.start(ms)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        _windows.append(self)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle("Media Studio")
        self.setAcceptDrops(True)
        avail = QApplication.primaryScreen().availableGeometry()
        w, h = min(1360, int(avail.width() * 0.9)), min(860, int(avail.height() * 0.9))
        self.setGeometry(avail.x() + (avail.width() - w) // 2, avail.y() + (avail.height() - h) // 2, w, h)
        self.kind = "empty"
        self.path = None
        self.source = None
        self.audio_track = None
        self.player = None
        self.playing = False
        self.cur_t = 0.0
        self.frame_t = 0.0
        self.speed = 1.0
        self.loop = False
        self._use_audio = False
        self._origin = (0.0, 0.0)
        self._resume_after_scrub = False
        self._last_label = 0.0
        self._folder_files = []
        self._exported = False

        self._build()
        self._shortcuts()
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.setInterval(4)
        self.timer.timeout.connect(self._tick)
        self._set_mode("empty")

    # ================================================================== UI
    def _build(self):
        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        v = QVBoxLayout(root)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # ---- top bar
        top = QFrame()
        top.setObjectName("TopBar")
        top.setFixedHeight(52)
        h = QHBoxLayout(top)
        h.setContentsMargins(10, 6, 10, 6)
        h.setSpacing(4)
        self.btn_open = tool_button("open", "Open (Ctrl+O)")
        self.btn_open.clicked.connect(self.open_dialog)
        self.btn_prev = tool_button("prev", "Previous file in folder (← / PgUp)")
        self.btn_prev.clicked.connect(lambda: self.step_file(-1))
        self.btn_next = tool_button("next", "Next file in folder (→ / PgDn)")
        self.btn_next.clicked.connect(lambda: self.step_file(1))
        for b in (self.btn_open, self.btn_prev, self.btn_next):
            h.addWidget(b)
        h.addSpacing(8)
        names = QVBoxLayout()
        names.setSpacing(0)
        self.lbl_name = QLabel("Media Studio")
        self.lbl_name.setObjectName("FileName")
        self.lbl_meta = QLabel("Open an image, GIF, video or audio file")
        self.lbl_meta.setObjectName("FileMeta")
        names.addWidget(self.lbl_name)
        names.addWidget(self.lbl_meta)
        h.addLayout(names)
        h.addStretch(1)

        self.btn_undo = tool_button("undo", "Undo (Ctrl+Z)")
        self.btn_undo.clicked.connect(self.undo)
        self.btn_redo = tool_button("redo", "Redo (Ctrl+Y)")
        self.btn_redo.clicked.connect(self.redo)
        h.addWidget(self.btn_undo)
        h.addWidget(self.btn_redo)
        self.sep_rotate = separator()
        h.addWidget(self.sep_rotate)
        self.btn_rotate = tool_button("rotate", "Rotate right (Ctrl+R)")
        self.btn_rotate.clicked.connect(lambda: self.canvas.rotate(True))
        self.btn_flip = tool_button("flip", "Flip / rotate")
        m = QMenu(self)
        m.addAction(theme.icon("rotate"), "Rotate right\tCtrl+R", lambda: self.canvas.rotate(True))
        m.addAction("Rotate left\tCtrl+Shift+R", lambda: self.canvas.rotate(False))
        m.addSeparator()
        m.addAction(theme.icon("flip"), "Flip horizontal", lambda: self.canvas.flip(True))
        m.addAction("Flip vertical", lambda: self.canvas.flip(False))
        self.btn_flip.setMenu(m)
        self.btn_flip.setPopupMode(QToolButton.InstantPopup)
        h.addWidget(self.btn_rotate)
        h.addWidget(self.btn_flip)
        h.addWidget(separator())
        self.btn_zoom_out = tool_button("zoomout", "Zoom out (Ctrl+-)")
        self.btn_zoom_out.clicked.connect(lambda: self.canvas.zoom_step(-1))
        self.lbl_zoom = QLabel("100%")
        self.lbl_zoom.setObjectName("ZoomLabel")
        self.lbl_zoom.setAlignment(Qt.AlignCenter)
        self.lbl_zoom.setToolTip("Zoom — Ctrl+0 fit, Ctrl+1 actual size")
        self.btn_zoom_in = tool_button("zoomin", "Zoom in (Ctrl+=)")
        self.btn_zoom_in.clicked.connect(lambda: self.canvas.zoom_step(1))
        self.btn_fit = tool_button("fit", "Fit to window (Ctrl+0)")
        self.btn_fit.clicked.connect(lambda: self.canvas.fit())
        self.lbl_zoom.setFixedHeight(28)
        for b in (self.btn_zoom_out, self.lbl_zoom, self.btn_zoom_in, self.btn_fit):
            h.addWidget(b)
        self.sep_copy = separator()
        h.addWidget(self.sep_copy)
        self.btn_copy = tool_button("copy", "Copy image (Ctrl+C)")
        self.btn_copy.clicked.connect(self.copy_image)
        self.btn_save = tool_button("save", "Save")
        sm = QMenu(self)
        sm.addAction(theme.icon("save"), "Save a copy…\tCtrl+S", self.save_copy)
        sm.addAction("Overwrite original\tCtrl+Shift+S", self.save_overwrite)
        self.btn_save.setMenu(sm)
        self.btn_save.setPopupMode(QToolButton.InstantPopup)
        h.addWidget(self.btn_copy)
        h.addWidget(self.btn_save)
        self.btn_export = QToolButton()
        self.btn_export.setText("  Export")
        self.btn_export.setIcon(theme.icon("export", 16, "#ffffff"))
        self.btn_export.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.btn_export.setStyleSheet(
            f"QToolButton {{ background: {theme.ACCENT}; color: white; font-weight: 600; padding: 6px 14px; border-radius: 8px; }}"
            f"QToolButton:hover {{ background: {theme.ACCENT_HOVER}; }}")
        self.btn_export.setFocusPolicy(Qt.NoFocus)
        self.btn_export.setCursor(Qt.PointingHandCursor)
        self.btn_export.setToolTip("Export trimmed / cropped media (Ctrl+E)")
        self.btn_export.clicked.connect(self.export)
        h.addSpacing(4)
        h.addWidget(self.btn_export)
        self.btn_more = tool_button("more", "More")
        self.btn_more.setPopupMode(QToolButton.InstantPopup)
        self.more_menu = QMenu(self)
        self.more_menu.aboutToShow.connect(self._fill_more_menu)
        self.btn_more.setMenu(self.more_menu)
        h.addWidget(self.btn_more)
        v.addWidget(top)

        # ---- options bar
        self.opts = QFrame()
        self.opts.setObjectName("OptionsBar")
        self.opts.setFixedHeight(44)
        oh = QHBoxLayout(self.opts)
        oh.setContentsMargins(14, 4, 14, 4)
        oh.setSpacing(6)
        self.opt_hint = QLabel("")
        self.opt_hint.setObjectName("Muted")
        oh.addWidget(self.opt_hint)
        # draw options
        self.draw_opts = QWidget()
        dh = QHBoxLayout(self.draw_opts)
        dh.setContentsMargins(0, 0, 0, 0)
        dh.setSpacing(5)
        self.swatch_group = QButtonGroup(self)
        for c in SWATCHES:
            b = QToolButton()
            b.setCheckable(True)
            b.setFixedSize(24, 24)
            b.setFocusPolicy(Qt.NoFocus)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(c)
            b.setStyleSheet(
                f"QToolButton {{ background: {c}; border-radius: 12px; border: 2px solid {theme.BORDER}; padding: 0; }}"
                f"QToolButton:hover {{ border: 2px solid #8a8f9a; }}"
                f"QToolButton:checked {{ border: 2px solid white; }}")
            b.clicked.connect(lambda _=False, col=c: self._set_color(QColor(col)))
            self.swatch_group.addButton(b)
            dh.addWidget(b)
        self.swatch_group.buttons()[0].setChecked(True)
        self.btn_custom_color = tool_button("", "Custom color…", size=16)
        self.btn_custom_color.clicked.connect(self._pick_color)
        dh.addWidget(self.btn_custom_color)
        dh.addSpacing(14)
        lbl = QLabel("Size")
        lbl.setObjectName("Muted")
        dh.addWidget(lbl)
        self.size_slider = QSlider(Qt.Horizontal)
        self.size_slider.setRange(1, 40)
        self.size_slider.setValue(5)
        self.size_slider.setFixedWidth(150)
        self.size_slider.setFocusPolicy(Qt.NoFocus)
        self.size_val = QLabel("5")
        self.size_val.setFixedWidth(22)
        self.size_slider.valueChanged.connect(self._set_stroke)
        dh.addWidget(self.size_slider)
        dh.addWidget(self.size_val)
        oh.addWidget(self.draw_opts)
        # crop options
        self.crop_opts = QWidget()
        ch = QHBoxLayout(self.crop_opts)
        ch.setContentsMargins(0, 0, 0, 0)
        ch.setSpacing(8)
        lbl = QLabel("Aspect")
        lbl.setObjectName("Muted")
        ch.addWidget(lbl)
        self.aspect = QComboBox()
        self.aspect.setFocusPolicy(Qt.NoFocus)
        for name, _ in ASPECTS:
            self.aspect.addItem(name)
        self.aspect.currentIndexChanged.connect(self._aspect_changed)
        ch.addWidget(self.aspect)
        self.crop_note = QLabel("")
        self.crop_note.setObjectName("Muted")
        ch.addSpacing(8)
        ch.addWidget(self.crop_note)
        self.btn_crop_cancel = QToolButton()
        self.btn_crop_cancel.setText("Reset")
        self.btn_crop_cancel.setFocusPolicy(Qt.NoFocus)
        self.btn_crop_cancel.clicked.connect(self._crop_cancel)
        self.btn_crop_apply = QToolButton()
        self.btn_crop_apply.setText("  Apply crop")
        self.btn_crop_apply.setIcon(theme.icon("check", 14, "#ffffff"))
        self.btn_crop_apply.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.btn_crop_apply.setFocusPolicy(Qt.NoFocus)
        self.btn_crop_apply.setStyleSheet(
            f"QToolButton {{ background: {theme.ACCENT}; color: white; font-weight: 600; padding: 4px 12px; border-radius: 7px; }}"
            f"QToolButton:hover {{ background: {theme.ACCENT_HOVER}; }}")
        self.btn_crop_apply.clicked.connect(self._crop_apply)
        ch.addWidget(self.btn_crop_cancel)
        ch.addWidget(self.btn_crop_apply)
        oh.addWidget(self.crop_opts)
        oh.addStretch(1)
        v.addWidget(self.opts)

        # ---- middle: rail + canvas
        mid = QHBoxLayout()
        mid.setContentsMargins(0, 0, 0, 0)
        mid.setSpacing(0)
        self.rail = QFrame()
        self.rail.setObjectName("ToolRail")
        self.rail.setFixedWidth(52)
        rv = QVBoxLayout(self.rail)
        rv.setContentsMargins(7, 10, 7, 10)
        rv.setSpacing(4)
        self.tool_group = QButtonGroup(self)
        self.tool_buttons = {}
        for t in TOOLS:
            if t is None:
                rv.addSpacing(4)
                rv.addWidget(separator(False), 0, Qt.AlignHCenter)
                rv.addSpacing(4)
                continue
            name, tip, _ = t
            b = tool_button(name, tip, checkable=True, size=19)
            b.setFixedSize(38, 38)
            b.clicked.connect(lambda _=False, n=name: self.set_tool(n))
            self.tool_group.addButton(b)
            self.tool_buttons[name] = b
            rv.addWidget(b)
        rv.addStretch(1)
        self.tool_buttons["move"].setChecked(True)
        mid.addWidget(self.rail)

        self.canvas_host = QWidget()
        chl = QVBoxLayout(self.canvas_host)
        chl.setContentsMargins(0, 0, 0, 0)
        self.canvas = Canvas()
        chl.addWidget(self.canvas)
        self.toast = Toast(self.canvas)
        mid.addWidget(self.canvas_host, 1)
        midw = QWidget()
        midw.setLayout(mid)
        v.addWidget(midw, 1)

        self.canvas.zoomChanged.connect(lambda z: self.lbl_zoom.setText(f"{z:.0f}%"))
        self.canvas.cursorInfo.connect(lambda s: self.st_cursor.setText(s))
        self.canvas.historyChanged.connect(self._update_actions)
        self.canvas.cropChanged.connect(self._crop_changed)
        self.canvas.needsHiRes.connect(self._hires)
        self.canvas.openRequested.connect(self.open_dialog)

        # ---- transport + timeline
        self.bottom = QWidget()
        bl = QVBoxLayout(self.bottom)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(0)
        tr = QFrame()
        tr.setObjectName("Transport")
        tr.setFixedHeight(58)
        th = QHBoxLayout(tr)
        th.setContentsMargins(12, 6, 12, 6)
        th.setSpacing(4)
        self.btn_start = tool_button("start", "Go to start (Home)")
        self.btn_start.clicked.connect(lambda: self.seek(0))
        self.btn_back = tool_button("stepback", "Previous frame (←)   Shift+← = 1 second")
        self.btn_back.setAutoRepeat(True)
        self.btn_back.clicked.connect(lambda: self.step_frames(-1))
        self.btn_play = tool_button("play", "Play / pause (Space)", size=18, color="#ffffff")
        self.btn_play.setObjectName("PlayButton")
        self.btn_play.setFixedSize(40, 40)
        self.btn_play.clicked.connect(self.toggle_play)
        self.btn_fwd = tool_button("stepfwd", "Next frame (→)   Shift+→ = 1 second")
        self.btn_fwd.setAutoRepeat(True)
        self.btn_fwd.clicked.connect(lambda: self.step_frames(1))
        self.btn_end = tool_button("end", "Go to end (End)")
        self.btn_end.clicked.connect(lambda: self.seek(self.source.duration if self.source else 0))
        for b in (self.btn_start, self.btn_back, self.btn_play, self.btn_fwd, self.btn_end):
            th.addWidget(b)
        th.addSpacing(10)
        tl = QVBoxLayout()
        tl.setSpacing(0)
        self.lbl_time = QLabel("0:00.00 / 0:00.00")
        self.lbl_time.setObjectName("TimeLabel")
        self.lbl_frame = QLabel("Frame 1 / 1")
        self.lbl_frame.setObjectName("FrameLabel")
        tl.addWidget(self.lbl_time)
        tl.addWidget(self.lbl_frame)
        th.addLayout(tl)
        th.addStretch(1)
        self.btn_split = tool_button("cut", "Split at playhead (S)")
        self.btn_split.clicked.connect(self.split)
        self.btn_trim_l = tool_button("trimleft", "Remove everything before the playhead (Q)")
        self.btn_trim_l.clicked.connect(lambda: self.timeline.trim_before(self.cur_t))
        self.btn_trim_r = tool_button("trimright", "Remove everything after the playhead (W)")
        self.btn_trim_r.clicked.connect(lambda: self.timeline.trim_after(self.cur_t + 1.0 / self.source.fps if self.source else 0))
        self.btn_del = tool_button("delete", "Remove selected segment (Delete)")
        self.btn_del.clicked.connect(self.delete_segment)
        self.btn_loop = tool_button("loop", "Loop playback", checkable=True)
        self.btn_loop.toggled.connect(lambda on: setattr(self, "loop", on))
        for b in (self.btn_split, self.btn_trim_l, self.btn_trim_r, self.btn_del):
            th.addWidget(b)
        th.addWidget(separator())
        th.addWidget(self.btn_loop)
        th.addStretch(1)
        self.btn_tl_out = tool_button("zoomout", "Zoom timeline out (wheel over timeline)", size=16)
        self.btn_tl_out.clicked.connect(lambda: self.timeline.zoom(1 / 1.6))
        self.btn_tl_fit = tool_button("fit", "Fit timeline", size=16)
        self.btn_tl_fit.clicked.connect(lambda: self.timeline.fit_view())
        self.btn_tl_in = tool_button("zoomin", "Zoom timeline in (wheel over timeline)", size=16)
        self.btn_tl_in.clicked.connect(lambda: self.timeline.zoom(1.6))
        for b in (self.btn_tl_out, self.btn_tl_fit, self.btn_tl_in):
            th.addWidget(b)
        th.addWidget(separator())
        self.btn_snap = tool_button("camera", "Frame snapshot")
        self.btn_snap.setPopupMode(QToolButton.InstantPopup)
        snm = QMenu(self)
        snm.addAction(theme.icon("copy"), "Copy frame", self.copy_frame)
        snm.addAction(theme.icon("save"), "Save frame as image…", self.save_frame)
        snm.addAction(theme.icon("pen"), "Annotate frame in new window", self.edit_frame)
        self.btn_snap.setMenu(snm)
        th.addWidget(self.btn_snap)
        self.speed_box = QComboBox()
        self.speed_box.setFocusPolicy(Qt.NoFocus)
        for s in ("0.25×", "0.5×", "1×", "1.5×", "2×"):
            self.speed_box.addItem(s, float(s[:-1]))
        self.speed_box.setCurrentIndex(2)
        self.speed_box.setToolTip("Playback speed (audio plays at 1× only)")
        self.speed_box.currentIndexChanged.connect(self._speed_changed)
        th.addWidget(self.speed_box)
        self.btn_mute = tool_button("volume", "Mute (M)")
        self.btn_mute.clicked.connect(self.toggle_mute)
        self.vol = QSlider(Qt.Horizontal)
        self.vol.setRange(0, 100)
        self.vol.setValue(80)
        self.vol.setFixedWidth(100)
        self.vol.setFocusPolicy(Qt.NoFocus)
        self.vol.valueChanged.connect(self._volume_changed)
        th.addWidget(self.btn_mute)
        th.addWidget(self.vol)
        bl.addWidget(tr)
        self.timeline = Timeline()
        self.timeline.seekRequested.connect(self.seek)
        self.timeline.scrubStarted.connect(self._scrub_start)
        self.timeline.scrubEnded.connect(self._scrub_end)
        self.timeline.editsChanged.connect(self._edits_changed)
        bl.addWidget(self.timeline)
        v.addWidget(self.bottom)

        # ---- status bar
        sb = QFrame()
        sb.setObjectName("StatusBar")
        sb.setFixedHeight(28)
        sh = QHBoxLayout(sb)
        sh.setContentsMargins(14, 0, 14, 0)
        self.st_info = QLabel("")
        self.st_cursor = QLabel("")
        self.st_hint = QLabel("")
        sh.addWidget(self.st_info)
        sh.addSpacing(24)
        sh.addWidget(self.st_cursor)
        sh.addStretch(1)
        sh.addWidget(self.st_hint)
        v.addWidget(sb)

    def _shortcuts(self):
        def sc(keys, fn):
            for k in (keys if isinstance(keys, (list, tuple)) else [keys]):
                s = QShortcut(QKeySequence(k), self)
                s.setContext(Qt.WindowShortcut)
                s.activated.connect(fn)
        sc("Ctrl+O", self.open_dialog)
        sc("Ctrl+S", self.save_copy)
        sc("Ctrl+Shift+S", self.save_overwrite)
        sc("Ctrl+C", self.copy_image)
        sc("Ctrl+Z", self.undo)
        sc(["Ctrl+Y", "Ctrl+Shift+Z"], self.redo)
        sc("Ctrl+0", lambda: self.canvas.fit())
        sc("Ctrl+1", lambda: self.canvas.actual_size())
        sc(["Ctrl+=", "Ctrl++"], lambda: self.canvas.zoom_step(1))
        sc("Ctrl+-", lambda: self.canvas.zoom_step(-1))
        sc("Ctrl+R", lambda: self.canvas.rotate(True))
        sc("Ctrl+Shift+R", lambda: self.canvas.rotate(False))
        sc("Ctrl+E", self.export)
        sc("F11", self.toggle_fullscreen)
        sc("Escape", self.escape)

    # ================================================================== mode / state
    def _set_mode(self, kind):
        self.kind = kind
        media = kind in ("video", "anim", "audio")
        has_frames = kind in ("image", "video", "anim")
        self.bottom.setVisible(media)
        self.rail.setVisible(kind != "audio")
        self.st_cursor.setText("")
        for name, b in self.tool_buttons.items():
            b.setEnabled(kind == "image" or (name in ("move", "crop") and kind in ("video", "anim")))
        if self.canvas.tool not in ("move", "crop") and kind != "image":
            self.set_tool("move")
        if kind == "audio" or kind == "empty":
            self.set_tool("move")
        for b in (self.btn_rotate, self.btn_flip, self.btn_copy, self.btn_save, self.sep_rotate, self.sep_copy):
            b.setVisible(kind in ("image", "empty"))
            b.setEnabled(kind == "image")
        for b in (self.btn_zoom_in, self.btn_zoom_out, self.btn_fit):
            b.setEnabled(has_frames)
        self.lbl_zoom.setVisible(has_frames)
        self.btn_export.setVisible(media)
        self.btn_snap.setEnabled(kind in ("video", "anim"))
        audio = media and self.source is not None and self.source.has_audio
        self.btn_mute.setVisible(audio)
        self.vol.setVisible(audio)
        self.btn_prev.setEnabled(kind != "empty")
        self.btn_next.setEnabled(kind != "empty")
        self._update_options()
        self._update_actions()
        if kind == "image":
            self.st_hint.setText("Scroll to zoom  ·  drag to pan  ·  ← → next file  ·  double-click for 100%")
        elif media:
            self.st_hint.setText("Space play  ·  ← → frame  ·  S split  ·  Del remove  ·  Q/W trim  ·  wheel on timeline zooms")
        else:
            self.st_hint.setText("")

    def set_tool(self, name):
        if name not in self.tool_buttons or not self.tool_buttons[name].isEnabled():
            return
        self.tool_buttons[name].setChecked(True)
        self.canvas.set_tool(name)
        self._update_options()

    def _update_options(self):
        tool = self.canvas.tool
        drawing = self.kind == "image" and tool not in ("move", "crop", "eraser")
        crop = tool == "crop" and self.kind in ("image", "video", "anim")
        self.draw_opts.setVisible(drawing)
        self.crop_opts.setVisible(crop)
        hints = {
            "move": "Scroll to zoom · drag to pan · double-click toggles fit / 100%",
            "eraser": "Click or drag over an annotation to erase it",
            "redact": "Drag a box to pixelate sensitive areas",
            "text": "Click where the text should go, type, then press Enter",
        }
        self.opt_hint.setText("" if drawing or crop else hints.get(tool, ""))
        if tool in ("text", "redact") and drawing:
            self.opt_hint.setText(hints[tool])
        self.opt_hint.setVisible(bool(self.opt_hint.text()))
        self.opts.setVisible(self.kind in ("image", "video", "anim"))
        if crop:
            image = self.kind == "image"
            self.btn_crop_apply.setVisible(image)
            self.btn_crop_cancel.setText("Reset" if image else "Clear crop")
            self._crop_changed()

    def _crop_changed(self):
        r = self.canvas.crop_pixels()
        if self.kind == "image":
            self.crop_note.setText("Enter to apply · Esc to cancel")
        else:
            self.crop_note.setText(f"Export will be cropped to {r.width()}×{r.height()}" if r else
                                   "Drag a box — the crop is applied when you export")

    def _update_actions(self):
        if self.kind == "image":
            self.btn_undo.setEnabled(bool(self.canvas.undo_stack))
            self.btn_redo.setEnabled(bool(self.canvas.redo_stack))
            name = Path(self.path).name if self.path else self.lbl_name.text().lstrip("● ")
            self.lbl_name.setText(("● " if self.canvas.dirty else "") + name)
        elif self.kind in ("video", "anim", "audio"):
            self.btn_undo.setEnabled(bool(self.timeline.undo_stack))
            self.btn_redo.setEnabled(bool(self.timeline.redo_stack))
        else:
            self.btn_undo.setEnabled(False)
            self.btn_redo.setEnabled(False)

    # ================================================================== opening
    def open_dialog(self):
        exts = " ".join(f"*{e}" for e in sorted(ALL_EXTS))
        start = str(Path(self.path).parent) if self.path else ""
        path, _ = QFileDialog.getOpenFileName(
            self, "Open media", start,
            f"All media ({exts});;Images ({' '.join('*' + e for e in sorted(IMAGE_EXTS))});;"
            f"Videos ({' '.join('*' + e for e in sorted(VIDEO_EXTS))});;"
            f"Audio ({' '.join('*' + e for e in sorted(AUDIO_EXTS))});;All files (*)")
        if path:
            self.open_path(path)

    def open_path(self, path, confirm=True):
        path = str(Path(path).resolve())
        if confirm and not self._confirm_discard():
            return False
        self._close_media()
        self._reset_tools()
        ext = Path(path).suffix.lower()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            if ext in VIDEO_EXTS or ext in AUDIO_EXTS:
                self._open_av(path)
            else:
                try:
                    img, anim = load_image(path)
                except Exception:
                    img = anim = None
                if anim is not None:
                    self._open_anim(path, anim)
                elif img is not None and not img.isNull():
                    self._open_image(img, path)
                else:
                    self._open_av(path)
            self._scan_folder()
            return True
        except Exception as e:
            self._close_media()
            self._set_mode("empty")
            self.canvas.set_placeholder("empty")
            self.lbl_name.setText(Path(path).name)
            self.lbl_meta.setText("Could not open this file")
            QMessageBox.warning(self, "Can't open file", f"{Path(path).name}\n\n{e}")
            return False
        finally:
            QApplication.restoreOverrideCursor()

    def _reset_tools(self):
        self.canvas.set_tool("move")
        self.tool_buttons["move"].setChecked(True)
        self.canvas.clear_crop()
        self.aspect.blockSignals(True)
        self.aspect.setCurrentIndex(0)
        self.aspect.blockSignals(False)
        self.canvas.crop_aspect = None

    def open_qimage(self, img: QImage, title):
        self._close_media()
        self._reset_tools()
        self.path = None
        self.canvas.set_image(img)
        self._set_mode("image")
        self.lbl_name.setText(title)
        self.lbl_meta.setText(f"{img.width()} × {img.height()}  ·  unsaved frame")
        self.st_info.setText(f"{img.width()} × {img.height()}")
        self.setWindowTitle(f"{title} — Media Studio")

    def _open_image(self, img, path):
        self.path = path
        self.canvas.set_image(img)
        self._set_mode("image")
        p = Path(path)
        size = human_size(p.stat().st_size)
        self.lbl_name.setText(p.name)
        self.lbl_meta.setText(f"{img.width()} × {img.height()}  ·  {size}  ·  {p.suffix[1:].upper()}")
        self.st_info.setText(f"{img.width()} × {img.height()}  ·  {size}")
        self.setWindowTitle(f"{p.name} — Media Studio")

    def _open_anim(self, path, pil):
        from .media import AnimSource
        src = AnimSource(path, pil)
        self._attach_source(src, path)
        self.btn_loop.setChecked(True)

    def _open_av(self, path):
        from .media import AudioOnlySource, AudioTrack, VideoSource
        try:
            src = VideoSource(path)
        except (ValueError, StopIteration):
            src = AudioOnlySource(path)
        self._attach_source(src, path)
        if src.has_audio:
            from .media import AudioPlayer
            if self.player is None:
                self.player = AudioPlayer()
                self.player.volume = self.vol.value() / 100
            self.audio_track = AudioTrack(path)
            self.audio_track.loaded.connect(self._audio_loaded)
        self.btn_loop.setChecked(False)

    def _attach_source(self, src, path):
        self.path = path
        self.source = src
        self._exported = False
        p = Path(path)
        kind = src.kind
        self.cur_t = self.frame_t = 0.0
        src.frameReady.connect(self._on_frame)
        src.thumbReady.connect(self._on_thumbs)
        self.timeline.load(src.duration, src.fps, src.snap, kind in ("video", "anim"), src.has_audio, p.stem)
        if kind == "anim":
            self.timeline.set_thumbs(src.thumbs)
        self._set_mode(kind)
        size = human_size(p.stat().st_size)
        if kind == "audio":
            self.canvas.set_placeholder("audio", p.name)
            meta = f"{src.codec.replace('float', '').upper()}  ·  {fmt_time(src.duration)}  ·  {size}"
        else:
            src.set_hint(self._initial_hint(src.size))
            self.canvas.mode = "video"
            meta = (f"{src.size.width()} × {src.size.height()}  ·  {src.fps:.3g} fps  ·  {src.codec.upper()}  ·  "
                    f"{fmt_time(src.duration)}  ·  {size}")
            src.request(0.0)
        self.lbl_name.setText(p.name)
        self.lbl_meta.setText(meta)
        self.st_info.setText(meta)
        self.setWindowTitle(f"{p.name} — Media Studio")
        self._update_time_labels(force=True)

    def _initial_hint(self, size):
        dpr = self.canvas.devicePixelRatioF()
        cw, ch = max(200, self.canvas.width()), max(150, self.canvas.height())
        s = min(1.0, cw * dpr / size.width(), ch * dpr / size.height())
        return QSize(int(size.width() * s), int(size.height() * s))

    def _close_media(self):
        if self.playing:
            self.pause(settle=False)
        self.timer.stop()
        if self.source is not None:
            try:
                self.source.frameReady.disconnect(self._on_frame)
            except Exception:
                pass
            self.source.close()
            self.source = None
        if self.audio_track is not None:
            self.audio_track.close()
            self.audio_track = None
        if self.player is not None:
            self.player.set_data(None)
        self._use_audio = False

    def _scan_folder(self):
        if not self.path:
            self._folder_files = []
            return
        folder = Path(self.path).parent
        try:
            files = [f.name for f in folder.iterdir() if f.suffix.lower() in ALL_EXTS and f.is_file()]
        except OSError:
            files = []
        self._folder_files = sorted(files, key=natural_key)

    def step_file(self, d):
        if not self.path or not self._folder_files:
            return
        name = Path(self.path).name
        files = self._folder_files
        i = files.index(name) if name in files else 0
        n = len(files)
        if n < 2:
            return
        if not self._confirm_discard():
            return
        folder = Path(self.path).parent
        for k in range(1, n):
            cand = folder / files[(i + d * k) % n]
            if cand.exists():
                self.open_path(str(cand), confirm=False)
                pos = self._folder_files.index(cand.name) + 1 if cand.name in self._folder_files else 0
                self.toast.show_msg(f"{pos} / {len(self._folder_files)}   {cand.name}", 1200)
                return

    def _confirm_discard(self):
        if self.kind == "image" and self.canvas.dirty:
            r = QMessageBox.question(self, "Unsaved changes", "Save your changes to this image first?",
                                     QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Save)
            if r == QMessageBox.Cancel:
                return False
            if r == QMessageBox.Save:
                return self.save_copy()
        if self.kind in ("video", "anim", "audio") and self.timeline.edited and not self._exported:
            r = QMessageBox.question(self, "Discard edits?", "Your cuts haven't been exported yet. Discard them?",
                                     QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Cancel)
            return r == QMessageBox.Discard
        return True

    # ================================================================== image actions
    def _set_color(self, c):
        self.canvas.color = c

    def _pick_color(self):
        c = QColorDialog.getColor(self.canvas.color, self, "Annotation color")
        if c.isValid():
            self.canvas.color = c
            self.swatch_group.setExclusive(False)
            for b in self.swatch_group.buttons():
                b.setChecked(False)
            self.swatch_group.setExclusive(True)

    def _set_stroke(self, v):
        self.canvas.stroke = v
        self.size_val.setText(str(v))

    def _aspect_changed(self, i):
        a = ASPECTS[i][1]
        if a == "orig":
            a = self.canvas.logical.width() / max(1, self.canvas.logical.height())
        self.canvas.set_crop_aspect(a)

    def _crop_apply(self):
        self.canvas.apply_crop()
        self.set_tool("move")

    def _crop_cancel(self):
        if self.kind == "image":
            self.canvas.crop_rect = None
            self.canvas.set_tool("crop")   # re-seeds a full-image crop box
            self.canvas.update()
        else:
            self.canvas.clear_crop()
            self.set_tool("move")

    def copy_image(self):
        if self.kind != "image":
            return
        QApplication.clipboard().setImage(self.canvas.render_result())
        self.toast.show_msg("Copied to clipboard")

    def _write_image(self, path):
        ext = Path(path).suffix.lower()
        flatten = "#ffffff" if ext in (".jpg", ".jpeg", ".bmp") else None
        img = self.canvas.render_result(flatten)
        if flatten:
            img = img.convertToFormat(QImage.Format_RGB32)
        quality = 95 if ext in (".jpg", ".jpeg", ".webp") else -1
        if not img.save(path, None, quality):
            QMessageBox.warning(self, "Save failed", f"Could not write {path}")
            return False
        self.canvas.mark_saved()
        self._update_actions()
        self.toast.show_msg(f"Saved  {Path(path).name}")
        return True

    def save_copy(self):
        if self.kind != "image":
            if self.kind in ("video", "anim", "audio"):
                self.export()
            return False
        if self.path:
            p = Path(self.path)
            ext = p.suffix.lower() if p.suffix.lower() in SAVE_EXTS else ".png"
            suggestion = str(p.with_name(p.stem + "_edited" + ext))
        else:
            suggestion = str(Path.home() / "Pictures" / (self.lbl_name.text().lstrip("● ").replace(" ", "_") + ".png"))
        path, _ = QFileDialog.getSaveFileName(self, "Save a copy", suggestion,
                                              "PNG (*.png);;JPEG (*.jpg *.jpeg);;WebP (*.webp);;BMP (*.bmp);;TIFF (*.tif *.tiff)")
        if not path:
            return False
        if Path(path).suffix.lower() not in SAVE_EXTS:
            path += ".png"
        return self._write_image(path)

    def save_overwrite(self):
        if self.kind != "image":
            return
        if not self.path or Path(self.path).suffix.lower() not in SAVE_EXTS:
            self.save_copy()
            return
        r = QMessageBox.question(self, "Overwrite original?",
                                 f"Replace {Path(self.path).name} with the edited version? This can't be undone.",
                                 QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
        if r == QMessageBox.Yes:
            self._write_image(self.path)

    def undo(self):
        if self.kind == "image":
            self.canvas.undo()
        elif self.kind in ("video", "anim", "audio"):
            self.timeline.undo()
        self._update_actions()

    def redo(self):
        if self.kind == "image":
            self.canvas.redo()
        elif self.kind in ("video", "anim", "audio"):
            self.timeline.redo()
        self._update_actions()

    def escape(self):
        if self.canvas.text_edit:
            self.canvas._cancel_text()
        elif self.canvas.tool == "crop":
            if self.kind == "image":
                self.canvas.clear_crop()
            self.set_tool("move")
        elif self.isFullScreen():
            self.toggle_fullscreen()
        elif self.timeline.selected is not None:
            self.timeline.selected = None
            self.timeline.update()

    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    # ================================================================== playback
    def _on_frame(self, t, img):
        if self.sender() is not self.source or self.playing:
            return
        self.frame_t = t
        self.canvas.set_frame(img, self.source.size)

    def _on_thumbs(self):
        if self.source is not None and self.sender() is self.source:
            self.timeline.set_thumbs(self.source.thumbs)

    def _audio_loaded(self):
        tr = self.audio_track
        if tr is None or self.sender() is not tr:
            return
        self.timeline.set_peaks(tr.peaks)
        if tr.data is not None and self.player is not None:
            self.player.set_data(tr.data)
            if self.playing:
                self._start_at(self.clock())

    def _hires(self):
        if self.source is None or self.kind not in ("video", "anim"):
            return
        self.source.set_hint(self.canvas.display_hint())
        if not self.playing and self.canvas.frame_too_small():
            self.source.request(self.cur_t)

    def _audio_ready(self):
        return (self.player is not None and self.player.active and self.audio_track is not None
                and self.audio_track.data is not None)

    def clock(self):
        if self._use_audio:
            return self.player.clock()
        t0, w0 = self._origin
        return t0 + (time.perf_counter() - w0) * self.speed

    def toggle_play(self):
        if self.playing:
            self.pause()
        else:
            self.play()

    def play(self):
        if self.source is None or self.kind not in ("video", "anim", "audio"):
            return
        tl = self.timeline
        if not tl.segments:
            self.toast.show_msg("Nothing to play — all segments removed")
            return
        t = self.cur_t
        if t >= tl.last_end() - 0.6 / self.source.fps:
            t = tl.first_start()
        if tl.segment_at(t) is None:
            t = tl.next_start(t)
            if t is None:
                t = tl.first_start()
        self.playing = True
        self._start_at(t)
        self.btn_play.setIcon(theme.icon("pause", 18, "#ffffff"))
        self.timer.start()

    def _start_at(self, t):
        self.cur_t = t
        self._origin = (t, time.perf_counter())
        self.source.play(t)
        self._use_audio = self._audio_ready() and abs(self.speed - 1.0) < 1e-6
        if self._use_audio:
            self.player.start(t)
        elif self.player is not None:
            self.player.stop()

    def pause(self, settle=True):
        if not self.playing:
            return
        self.playing = False
        self.timer.stop()
        self.source.stop()
        if self.player is not None:
            self.player.stop()
        self.btn_play.setIcon(theme.icon("play", 18, "#ffffff"))
        if settle:
            t = self.frame_t if self.kind in ("video", "anim") else self.cur_t
            self.cur_t = self.source.snap(t)
            self.source.request(self.cur_t)
            self.timeline.set_playhead(self.cur_t)
            self._update_time_labels(force=True)

    def _tick(self):
        if self.source is None:
            return
        t = self.clock()
        tl = self.timeline
        fps = self.source.fps
        ended = self.source.eof and self.source.queue_empty() and t > self.frame_t + 2.0 / fps \
            and self.kind != "audio"
        i = tl.segment_at(t)
        if i is None or ended:
            nxt = None if ended else tl.next_start(t)
            if nxt is None:
                if self.loop and tl.segments:
                    self._start_at(tl.first_start())
                else:
                    end = tl.last_end() if not ended else self.frame_t
                    self.pause(settle=False)
                    self.cur_t = self.source.snap(max(0.0, min(end, self.source.duration) - 0.5 / fps))
                    self.source.request(self.cur_t)
                    self.timeline.set_playhead(self.cur_t)
                    self._update_time_labels(force=True)
                return
            self._start_at(nxt)
            t = nxt
        r = self.source.take_frame(t)
        if r is not None:
            self.frame_t = r[0]
            self.canvas.set_frame(r[1], self.source.size)
        self.cur_t = t
        self.timeline.set_playhead(t, follow=True)
        self._update_time_labels()

    def _update_time_labels(self, force=False):
        now = time.perf_counter()
        if not force and now - self._last_label < 0.033:
            return
        self._last_label = now
        if self.source is None:
            return
        src = self.source
        t = self.cur_t
        self.lbl_time.setText(f"{fmt_time(t, src.fps, True)}  /  {fmt_time(src.duration, src.fps, True)}")
        if self.kind == "audio":
            self.lbl_frame.setText(f"{t:.2f} s")
        else:
            self.lbl_frame.setText(f"Frame {src.index(t) + 1} / {src.frame_count}")

    def seek(self, t):
        if self.source is None:
            return
        t = self.source.snap(max(0.0, min(t, self.source.duration)))
        if self.playing:
            self._start_at(t)
            return
        self.cur_t = t
        self.source.request(t)
        self.timeline.set_playhead(t, follow=True)
        self._update_time_labels(force=True)

    def step_frames(self, n):
        if self.source is None:
            return
        if self.playing:
            self.pause()
        self.seek(self.source.step(self.cur_t, n))

    def _scrub_start(self):
        self._resume_after_scrub = self.playing
        if self.playing:
            self.pause(settle=False)

    def _scrub_end(self):
        if self._resume_after_scrub:
            self._resume_after_scrub = False
            self.play()

    def _speed_changed(self, i):
        self.speed = self.speed_box.itemData(i)
        if self.playing:
            self._start_at(self.clock())

    def _volume_changed(self, v):
        if self.player is not None:
            self.player.volume = v / 100
            if v > 0 and self.player.muted:
                self.toggle_mute()

    def toggle_mute(self):
        if self.player is None:
            return
        self.player.muted = not self.player.muted
        self.btn_mute.setIcon(theme.icon("mute" if self.player.muted else "volume"))

    def split(self):
        if self.source is not None:
            if not self.timeline.split(self.cur_t):
                self.toast.show_msg("Move the playhead inside a segment to split it", 1600)

    def delete_segment(self):
        if self.source is None:
            return
        tl = self.timeline
        if tl.selected is None:
            tl.selected = tl.segment_at(self.cur_t)
        if tl.selected is not None:
            tl.delete_selected()

    def _edits_changed(self):
        self._exported = False
        self._update_actions()
        n = len(self.timeline.segments)
        if self.source is not None and self.timeline.edited:
            self.lbl_name.setText(f"{Path(self.path).name}")
            self.st_info.setText(f"{n} segment{'s' if n != 1 else ''} kept  ·  output "
                                 f"{fmt_time(self.timeline.kept_duration(), self.source.fps, True)}")

    # ================================================================== frames / export
    def _current_full_frame(self):
        if self.source is None or self.kind not in ("video", "anim"):
            return None
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            return self.source.full_frame(self.cur_t)
        finally:
            QApplication.restoreOverrideCursor()

    def copy_frame(self):
        img = self._current_full_frame()
        if img is not None:
            QApplication.clipboard().setImage(img)
            self.toast.show_msg("Frame copied to clipboard")

    def save_frame(self):
        img = self._current_full_frame()
        if img is None:
            return
        p = Path(self.path)
        idx = self.source.index(self.cur_t) + 1
        sug = str(p.with_name(f"{p.stem}_frame{idx:05d}.png"))
        path, _ = QFileDialog.getSaveFileName(self, "Save frame", sug, "PNG (*.png);;JPEG (*.jpg)")
        if path:
            if img.save(path, None, 95):
                self.toast.show_msg(f"Saved  {Path(path).name}")

    def edit_frame(self):
        img = self._current_full_frame()
        if img is None:
            return
        idx = self.source.index(self.cur_t) + 1
        w = MainWindow()
        w.resize(self.size())
        w.show()
        w.open_qimage(img, f"{Path(self.path).stem} — frame {idx}")
        w.set_tool("pen")

    def export(self):
        if self.source is None or self.kind not in ("video", "anim", "audio"):
            return
        if self.playing:
            self.pause()
        from .export import ExportDialog
        crop = self.canvas.crop_pixels() if self.kind != "audio" else None
        dlg = ExportDialog(self, self.source, self.timeline.segments, crop, self.source.has_audio)
        dlg.exec()
        if dlg.out_path and os.path.exists(dlg.out_path):
            self._exported = True

    # ================================================================== misc
    def _fill_more_menu(self):
        m = self.more_menu
        m.clear()
        m.addAction(theme.icon("open"), "Open…\tCtrl+O", self.open_dialog)
        m.addAction(theme.icon("fullscreen"), "Full screen\tF11", self.toggle_fullscreen)
        m.addSeparator()
        if assoc.exe_path():
            if assoc.is_registered():
                m.addAction(theme.icon("close"), "Remove from Windows ‘Open with’", self._unregister)
            else:
                m.addAction(theme.icon("check"), "Add to Windows ‘Open with’ menu", self._register)
        else:
            a = m.addAction("Add to ‘Open with’ (packaged .exe only)")
            a.setEnabled(False)
        m.addAction(theme.icon("settings"), "Windows default apps…", lambda: os.startfile("ms-settings:defaultapps"))
        m.addSeparator()
        m.addAction("Keyboard shortcuts", self._show_shortcuts)

    def _register(self):
        try:
            assoc.register()
            QMessageBox.information(
                self, "Added to ‘Open with’",
                "Media Studio now appears in the right-click ‘Open with’ menu for images, GIFs, videos and audio.\n\n"
                "To make it the default for a file type: right-click a file → Open with → Choose another app → "
                "Media Studio → ‘Always’. Or use Settings → Apps → Default apps → Media Studio.")
        except Exception as e:
            QMessageBox.warning(self, "Registration failed", str(e))

    def _unregister(self):
        try:
            assoc.unregister()
            self.toast.show_msg("Removed from ‘Open with’")
        except Exception as e:
            QMessageBox.warning(self, "Failed", str(e))

    def _show_shortcuts(self):
        QMessageBox.information(self, "Keyboard shortcuts", (
            "GENERAL\n"
            "Ctrl+O  open    ·    Ctrl+Z / Ctrl+Y  undo / redo    ·    F11  full screen\n"
            "Ctrl+0  fit    ·    Ctrl+1  100%    ·    Ctrl+= / Ctrl+-  zoom    ·    scroll  zoom at cursor\n\n"
            "IMAGES\n"
            "← / →  previous / next file in folder    ·    Space+drag or middle-drag  pan\n"
            "V pan · C crop · P pen · H highlighter · A arrow · L line · R rectangle\n"
            "E ellipse · T text · B pixelate · X eraser    ·    Shift  constrain shapes\n"
            "Enter  apply crop    ·    Ctrl+R  rotate    ·    Ctrl+C  copy    ·    Ctrl+S  save a copy\n\n"
            "VIDEO / GIF / AUDIO\n"
            "Space / K  play-pause    ·    ← / →  one frame    ·    Shift+← / →  one second\n"
            "Home / End  start / end    ·    J / L  back / forward 5 s    ·    PgUp / PgDn  prev / next file\n"
            "S  split at playhead    ·    Delete  remove segment    ·    Q / W  trim before / after playhead\n"
            "M  mute    ·    C  crop for export    ·    Ctrl+E  export\n"
            "Timeline: click/drag to scrub · drag segment edges to trim · wheel zooms · Shift+wheel scrolls"))

    def keyPressEvent(self, e):
        k = e.key()
        mods = e.modifiers()
        media = self.kind in ("video", "anim", "audio") and self.source is not None
        if mods & (Qt.ControlModifier | Qt.AltModifier):
            return super().keyPressEvent(e)
        if media:
            shift = bool(mods & Qt.ShiftModifier)
            fps = self.source.fps
            if k in (Qt.Key_Space, Qt.Key_K):
                self.toggle_play()
            elif k == Qt.Key_Left:
                self.step_frames(-int(round(fps)) if shift else -1)
            elif k == Qt.Key_Right:
                self.step_frames(int(round(fps)) if shift else 1)
            elif k == Qt.Key_Home:
                self.seek(0)
            elif k == Qt.Key_End:
                self.seek(self.source.duration)
            elif k == Qt.Key_J:
                self.seek(self.cur_t - 5)
            elif k == Qt.Key_L:
                self.seek(self.cur_t + 5)
            elif k == Qt.Key_S:
                self.split()
            elif k in (Qt.Key_Delete, Qt.Key_Backspace):
                self.delete_segment()
            elif k == Qt.Key_Q:
                self.timeline.trim_before(self.cur_t)
            elif k == Qt.Key_W:
                self.timeline.trim_after(self.cur_t + 1.0 / fps)
            elif k == Qt.Key_M:
                self.toggle_mute()
            elif k == Qt.Key_C and self.kind != "audio":
                self.set_tool("move" if self.canvas.tool == "crop" else "crop")
            elif k == Qt.Key_V:
                self.set_tool("move")
            elif k == Qt.Key_PageUp:
                self.step_file(-1)
            elif k == Qt.Key_PageDown:
                self.step_file(1)
            else:
                return super().keyPressEvent(e)
            return
        if self.kind == "image":
            if k in (Qt.Key_Left, Qt.Key_PageUp):
                self.step_file(-1)
                return
            if k in (Qt.Key_Right, Qt.Key_PageDown):
                self.step_file(1)
                return
            if k in (Qt.Key_Return, Qt.Key_Enter) and self.canvas.tool == "crop":
                self._crop_apply()
                return
            for t in TOOLS:
                if t and e.text().upper() == t[2]:
                    self.set_tool(t[0])
                    return
        super().keyPressEvent(e)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        urls = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if urls:
            self.open_path(urls[0])

    def showEvent(self, e):
        super().showEvent(e)
        if sys.platform == "win32":
            try:
                hwnd = int(self.winId())
                dwm = ctypes.windll.dwmapi
                on = ctypes.c_int(1)
                dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(on), 4)          # dark title bar
                col = QColor(theme.PANEL)
                ref = ctypes.c_int(col.red() | (col.green() << 8) | (col.blue() << 16))
                dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(ref), 4)         # caption color (Win11)
            except Exception:
                pass

    def closeEvent(self, e):
        if not self._confirm_discard():
            e.ignore()
            return
        self._close_media()
        if self.player is not None:
            self.player.close()
        if self in _windows:
            _windows.remove(self)
        super().closeEvent(e)
