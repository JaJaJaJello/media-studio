"""Export kept segments (with optional crop/scale) to MP4 / WebM / GIF / MP3 / WAV via ffmpeg."""
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QProgressBar, QPushButton, QVBoxLayout, QWidget)

from .timeline import fmt_time

NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

FORMATS = [
    # key, label, ext, has_video
    ("mp4", "MP4 video (H.264)", ".mp4", True),
    ("webm", "WebM video (VP9)", ".webm", True),
    ("gif", "Animated GIF", ".gif", True),
    ("mp3", "MP3 audio", ".mp3", False),
    ("wav", "WAV audio", ".wav", False),
]


def ffmpeg_exe():
    here = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
    for c in (here / "ffmpeg.exe", here / "_internal" / "ffmpeg.exe"):
        if c.exists():
            return str(c)
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    i = 2
    while True:
        p = path.with_name(f"{path.stem} ({i}){path.suffix}")
        if not p.exists():
            return p
        i += 1


class ExportJob(QObject):
    progress = Signal(float)
    finished = Signal(bool, str)

    def __init__(self, cmd=None, total=1.0, frame_feed=None, pil_job=None):
        super().__init__()
        self.cmd = cmd
        self.total = max(total, 1e-3)
        self.frame_feed = frame_feed     # generator of raw RGBA bytes for stdin
        self.pil_job = pil_job           # callable(progress_cb, cancelled) for Pillow GIF writing
        self.proc = None
        self.cancelled = False

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def cancel(self):
        self.cancelled = True
        if self.proc and self.proc.poll() is None:
            self.proc.kill()

    def _run(self):
        try:
            if self.pil_job:
                self.pil_job(self.progress.emit, lambda: self.cancelled)
                self.finished.emit(not self.cancelled, "Cancelled" if self.cancelled else "")
                return
            self.proc = subprocess.Popen(
                self.cmd, stdin=subprocess.PIPE if self.frame_feed else subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=NO_WINDOW)
            err_chunks = []
            threading.Thread(target=lambda: err_chunks.append(self.proc.stderr.read()), daemon=True).start()
            if self.frame_feed:
                threading.Thread(target=lambda: self.proc.stdout.read(), daemon=True).start()
                n = 0
                try:
                    for data, frac in self.frame_feed:
                        if self.cancelled:
                            break
                        self.proc.stdin.write(data)
                        n += 1
                        if n % 5 == 0:
                            self.progress.emit(frac)
                    self.proc.stdin.close()
                except (BrokenPipeError, OSError):
                    pass
            else:
                for line in self.proc.stdout:
                    m = re.match(rb"out_time_(?:us|ms)=(\d+)", line)
                    if m:
                        self.progress.emit(min(1.0, int(m.group(1)) / 1e6 / self.total))
            code = self.proc.wait()
            if self.cancelled:
                self.finished.emit(False, "Cancelled")
            elif code == 0:
                self.progress.emit(1.0)
                self.finished.emit(True, "")
            else:
                msg = b"".join(c for c in err_chunks if c).decode(errors="replace").strip()
                self.finished.emit(False, msg[-600:] or f"ffmpeg exited with code {code}")
        except Exception as e:
            self.finished.emit(False, str(e))


def qimage_to_pil(img: QImage):
    from PIL import Image
    img = img.convertToFormat(QImage.Format_RGBA8888)
    return Image.frombuffer("RGBA", (img.width(), img.height()), bytes(img.constBits()), "raw", "RGBA",
                            img.bytesPerLine(), 1).copy()


class ExportDialog(QDialog):
    def __init__(self, parent, source, segments, crop, has_audio):
        super().__init__(parent)
        self.setWindowTitle("Export")
        self.setMinimumWidth(520)
        self.source = source
        self.segments = [list(s) for s in segments]
        self.crop = crop
        self.has_audio = has_audio
        self.has_video = source.kind in ("video", "anim")
        self.job = None
        self.out_path = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(14)
        title = QLabel("Export")
        title.setStyleSheet("font-size: 16pt; font-weight: 600;")
        lay.addWidget(title)
        kept = sum(e - s for s, e in self.segments)
        dims = f"{source.size.width()}×{source.size.height()}" if self.has_video else "audio"
        if crop is not None:
            dims = f"{crop.width()}×{crop.height()} (cropped)"
        info = QLabel(f"{len(self.segments)} segment{'s' if len(self.segments) != 1 else ''}  ·  "
                      f"{fmt_time(kept, source.fps, True)}  ·  {dims}")
        info.setObjectName("Muted")
        lay.addWidget(info)

        form = QFormLayout()
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(10)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.fmt = QComboBox()
        for key, label, ext, needs_video in FORMATS:
            if needs_video and not self.has_video:
                continue
            if not needs_video and not has_audio:
                continue
            self.fmt.addItem(label, key)
        if source.kind == "anim":
            self.fmt.setCurrentIndex(self.fmt.findData("gif"))
        form.addRow("Format", self.fmt)
        self.quality = QComboBox()
        self.quality.addItems(["High", "Balanced", "Small file"])
        self.quality.setCurrentIndex(1)
        form.addRow("Quality", self.quality)
        self.size = QComboBox()
        form.addRow("Size", self.size)
        self.fps = QComboBox()
        form.addRow("Frame rate", self.fps)
        self.audio = QCheckBox("Include audio")
        self.audio.setChecked(True)
        self.audio.setVisible(has_audio and self.has_video)
        form.addRow("", self.audio)
        self.crop_cb = QCheckBox(f"Apply crop ({crop.width()}×{crop.height()})" if crop else "Apply crop")
        self.crop_cb.setChecked(crop is not None)
        self.crop_cb.setVisible(crop is not None)
        form.addRow("", self.crop_cb)
        row = QHBoxLayout()
        self.path_edit = QLineEdit()
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row.addWidget(self.path_edit, 1)
        row.addWidget(browse)
        w = QWidget()
        w.setLayout(row)
        row.setContentsMargins(0, 0, 0, 0)
        form.addRow("Save to", w)
        lay.addLayout(form)

        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(8)
        self.bar.hide()
        self.status = QLabel("")
        self.status.setObjectName("Muted")
        self.status.setWordWrap(True)
        lay.addWidget(self.bar)
        lay.addWidget(self.status)

        btns = QHBoxLayout()
        self.reveal_btn = QPushButton("Show in folder")
        self.reveal_btn.hide()
        self.reveal_btn.clicked.connect(self._reveal)
        btns.addWidget(self.reveal_btn)
        btns.addStretch(1)
        self.cancel_btn = QPushButton("Close")
        self.cancel_btn.clicked.connect(self._cancel)
        self.go_btn = QPushButton("Export")
        self.go_btn.setObjectName("Primary")
        self.go_btn.clicked.connect(self._start)
        btns.addWidget(self.cancel_btn)
        btns.addWidget(self.go_btn)
        lay.addLayout(btns)

        self.fmt.currentIndexChanged.connect(self._fmt_changed)
        self._fmt_changed()
        if kept <= 0:
            self.go_btn.setEnabled(False)
            self.status.setText("Nothing to export — every segment has been removed.")

    # ------------------------------------------------------------------ ui
    def _key(self):
        return self.fmt.currentData()

    def _ext(self):
        return next(f[2] for f in FORMATS if f[0] == self._key())

    def _fmt_changed(self):
        key = self._key()
        is_video = key in ("mp4", "webm", "gif")
        self.size.clear()
        self.fps.clear()
        src_h = self.crop.height() if self.crop else self.source.size.height()
        src_w = self.crop.width() if self.crop else self.source.size.width()
        if key == "gif":
            self.size.addItem(f"Original width ({src_w}px)", None)
            for wv in (1280, 960, 800, 640, 480, 320):
                if wv < src_w:
                    self.size.addItem(f"{wv}px wide", wv)
            self.size.setCurrentIndex(self.size.findData(640) if src_w > 800 and self.size.findData(640) >= 0 else 0)
            if self.source.kind == "anim":
                self.fps.addItem("Original timing", None)
            else:
                for f in (10, 12, 15, 20, 24, 30):
                    if f <= self.source.fps + 0.5:
                        self.fps.addItem(f"{f} fps", f)
                self.fps.setCurrentIndex(max(0, self.fps.findData(15)))
        elif is_video:
            self.size.addItem(f"Original ({src_w}×{src_h})", None)
            for hv in (2160, 1440, 1080, 720, 480, 360):
                if hv < src_h:
                    self.size.addItem(f"{hv}p", hv)
            if self.source.kind == "anim":
                self.fps.addItem(f"{min(60, round(self.source.fps))} fps (smooth)", None)
            else:
                self.fps.addItem(f"Source ({self.source.fps:.3g} fps)", None)
                for f in (60, 30, 24):
                    if f < self.source.fps - 0.5:
                        self.fps.addItem(f"{f} fps", f)
        for wdg in (self.size, self.fps):
            wdg.setEnabled(is_video)
        self.quality.setEnabled(key in ("mp4", "webm", "mp3"))
        self.audio.setEnabled(key in ("mp4", "webm"))
        self.crop_cb.setEnabled(is_video)
        src = Path(self.source.path)
        cur = self.path_edit.text().strip()
        base = Path(cur).with_suffix("") if cur else src.with_name(src.stem + "_edit")
        if not cur:
            self.path_edit.setText(str(unique_path(base.with_suffix(self._ext()))))
        else:
            self.path_edit.setText(str(base.with_suffix(self._ext())))

    def _browse(self):
        ext = self._ext()
        path, _ = QFileDialog.getSaveFileName(self, "Export as", self.path_edit.text(), f"*{ext}")
        if path:
            if not path.lower().endswith(ext):
                path += ext
            self.path_edit.setText(path)

    def _reveal(self):
        if self.out_path:
            subprocess.Popen(["explorer", "/select,", os.path.normpath(self.out_path)])

    def _cancel(self):
        if self.job:
            self.job.cancel()
        else:
            self.reject()

    # ------------------------------------------------------------------ run
    def _start(self):
        out = self.path_edit.text().strip()
        if not out:
            return
        out = str(Path(out).with_suffix(self._ext()))
        if os.path.normcase(os.path.abspath(out)) == os.path.normcase(os.path.abspath(self.source.path)):
            self.status.setText("Choose a different file name — the source can't be overwritten while it is open.")
            return
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        self.out_path = out
        try:
            if self.source.kind == "anim":
                self.job = self._anim_job(out)
            else:
                self.job = self._ffmpeg_job(out)
        except Exception as e:
            self.status.setText(f"Could not start export: {e}")
            return
        self.job.progress.connect(lambda f: self.bar.setValue(int(f * 1000)))
        self.job.finished.connect(self._done)
        self.go_btn.setEnabled(False)
        self.cancel_btn.setText("Cancel")
        self.bar.setValue(0)
        self.bar.show()
        self.reveal_btn.hide()
        self.status.setText("Exporting…")
        for w in (self.fmt, self.quality, self.size, self.fps, self.audio, self.crop_cb, self.path_edit):
            w.setEnabled(False)
        self.job.start()

    def _done(self, ok, msg):
        self.job = None
        self.go_btn.setEnabled(True)
        self.cancel_btn.setText("Close")
        for w in (self.fmt, self.path_edit):
            w.setEnabled(True)
        self._fmt_changed_keep_path()
        if ok:
            size = os.path.getsize(self.out_path) / (1024 * 1024)
            self.status.setText(f"Saved {Path(self.out_path).name}  ·  {size:.1f} MB")
            self.reveal_btn.show()
        else:
            self.bar.hide()
            try:
                if os.path.exists(self.out_path):
                    os.remove(self.out_path)
            except OSError:
                pass
            self.status.setText("Export cancelled." if msg == "Cancelled" else f"Export failed:\n{msg}")

    def _fmt_changed_keep_path(self):
        p = self.path_edit.text()
        self._fmt_changed()
        self.path_edit.setText(p)

    def _crop(self):
        if self.crop is not None and self.crop_cb.isChecked():
            r = self.crop
            return r.x(), r.y(), r.width() // 2 * 2, r.height() // 2 * 2
        return None

    def _ffmpeg_job(self, out):
        key = self._key()
        q = self.quality.currentIndex()
        v = key in ("mp4", "webm", "gif") and self.source.kind == "video"
        a = self.has_audio and (key in ("mp3", "wav") or (key in ("mp4", "webm") and self.audio.isChecked()))
        segs = self.segments
        off = segs[0][0] if segs[0][0] > 1.0 else 0.0
        cmd = [ffmpeg_exe(), "-y", "-hide_banner", "-nostdin", "-loglevel", "error", "-progress", "pipe:1", "-nostats"]
        if off:
            cmd += ["-ss", f"{off:.6f}"]
        cmd += ["-i", self.source.path]
        fc = []
        for i, (s, e) in enumerate(segs):
            s, e = s - off, e - off
            if v:
                fc.append(f"[0:v:0]trim=start={s:.6f}:end={e:.6f},setpts=PTS-STARTPTS[v{i}]")
            if a:
                fc.append(f"[0:a:0]atrim=start={s:.6f}:end={e:.6f},asetpts=PTS-STARTPTS[a{i}]")
        ins = "".join((f"[v{i}]" if v else "") + (f"[a{i}]" if a else "") for i in range(len(segs)))
        fc.append(f"{ins}concat=n={len(segs)}:v={int(v)}:a={int(a)}" + ("[vc]" if v else "") + ("[ac]" if a else ""))
        if v:
            post = []
            c = self._crop()
            if c:
                post.append(f"crop={c[2]}:{c[3]}:{c[0]}:{c[1]}")
            size = self.size.currentData()
            fps = self.fps.currentData()
            if key == "gif":
                if fps:
                    post.append(f"fps={fps}")
                if size:
                    post.append(f"scale={size}:-1:flags=lanczos")
                post.append("split[s0][s1]")
                fc.append("[vc]" + ",".join(post))
                fc.append("[s0]palettegen=stats_mode=diff[pal]")
                fc.append("[s1][pal]paletteuse=dither=sierra2_4a:diff_mode=rectangle[vout]")
            else:
                if size:
                    post.append(f"scale=-2:{size}:flags=lanczos")
                if fps:
                    post.append(f"fps={fps}")
                post.append("format=yuv420p")
                fc.append("[vc]" + ",".join(post) + "[vout]")
        cmd += ["-filter_complex", ";".join(fc)]
        if v:
            cmd += ["-map", "[vout]"]
        if a:
            cmd += ["-map", "[ac]"]
        if key == "mp4":
            cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", str((18, 23, 28)[q]), "-movflags", "+faststart"]
            if a:
                cmd += ["-c:a", "aac", "-b:a", ("256k", "192k", "128k")[q]]
        elif key == "webm":
            cmd += ["-c:v", "libvpx-vp9", "-crf", str((24, 32, 40)[q]), "-b:v", "0", "-row-mt", "1",
                    "-deadline", "good", "-cpu-used", "4"]
            if a:
                cmd += ["-c:a", "libopus", "-b:a", ("192k", "128k", "96k")[q]]
        elif key == "gif":
            cmd += ["-loop", "0"]
        elif key == "mp3":
            cmd += ["-c:a", "libmp3lame", "-b:a", ("320k", "192k", "128k")[q]]
        elif key == "wav":
            cmd += ["-c:a", "pcm_s16le"]
        cmd.append(out)
        total = sum(e - s for s, e in segs)
        return ExportJob(cmd=cmd, total=total)

    def _anim_plan(self):
        """(frame_index, display_duration) for every frame overlapping a kept segment."""
        src = self.source
        plan = []
        for i, (t, d) in enumerate(zip(src.times, src.durations)):
            for s, e in self.segments:
                ov = min(t + d, e) - max(t, s)
                if ov > 1e-4:
                    plan.append((i, ov))
        return plan

    def _anim_frame(self, i):
        img = self.source.frames[i]
        c = self._crop()
        if c:
            img = img.copy(c[0], c[1], c[2], c[3])
        return img

    def _anim_job(self, out):
        key = self._key()
        plan = self._anim_plan()
        if key == "gif":
            width = self.size.currentData()

            def job(progress, cancelled):
                from PIL import Image
                frames, durs = [], []
                for n, (i, d) in enumerate(plan):
                    if cancelled():
                        return
                    im = qimage_to_pil(self._anim_frame(i))
                    if width and im.width > width:
                        im = im.resize((width, max(1, round(im.height * width / im.width))), Image.LANCZOS)
                    frames.append(im)
                    durs.append(max(20, int(round(d * 1000))))
                    progress(0.6 * n / len(plan))
                progress(0.7)
                frames[0].save(out, save_all=True, append_images=frames[1:], duration=durs, loop=0,
                               disposal=2, optimize=False)
            return ExportJob(pil_job=job)

        fps = min(60, max(1, round(self.source.fps)))
        first = self._anim_frame(0)
        w, h = first.width() // 2 * 2, first.height() // 2 * 2
        size = self.size.currentData()
        total = sum(d for _, d in plan)

        def feed():
            acc, t_out, k = 0.0, 0.0, 0
            for i, d in plan:
                acc += d
                img = self._anim_frame(i).convertToFormat(QImage.Format_RGBA8888).copy(0, 0, w, h)
                data = bytes(img.constBits())[:w * h * 4] if img.bytesPerLine() == w * 4 else b"".join(
                    bytes(img.constScanLine(y))[:w * 4] for y in range(h))
                while t_out < acc - 1e-6:
                    yield data, t_out / total
                    t_out = (k := k + 1) / fps

        cmd = [ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgba",
               "-s", f"{w}x{h}", "-r", str(fps), "-i", "-"]
        vf = []
        if size:
            vf.append(f"scale=-2:{size}:flags=lanczos")
        vf.append("format=yuv420p")
        cmd += ["-vf", ",".join(vf)]
        q = self.quality.currentIndex()
        if key == "mp4":
            cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", str((18, 23, 28)[q]), "-movflags", "+faststart"]
        else:
            cmd += ["-c:v", "libvpx-vp9", "-crf", str((24, 32, 40)[q]), "-b:v", "0", "-row-mt", "1"]
        cmd.append(out)
        return ExportJob(cmd=cmd, total=total, frame_feed=feed())
