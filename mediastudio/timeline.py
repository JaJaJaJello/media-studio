"""Shotcut-style timeline: ruler, thumbnail track, waveform track, playhead, split/trim editing.

The timeline stays in *source* time. Edits are a list of kept [start, end] segments; removed
ranges are shown hatched and skipped during playback and export.
"""
import math

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QScrollBar, QWidget

from . import theme
from .media import AUDIO_RATE, PEAK_BLOCK


def fmt_time(t, fps=None, frames=False):
    t = max(0.0, t)
    h = int(t // 3600)
    m = int(t % 3600 // 60)
    s = int(t % 60)
    base = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
    if frames and fps:
        f = int(round((t - math.floor(t)) * fps))
        if f >= round(fps):
            f = 0
        base += f".{f:02d}"
    return base


def _hatch_brush():
    pm = QPixmap(10, 10)
    pm.fill(QColor("#121317"))
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(QColor("#22252c"), 2))
    p.drawLine(-2, 12, 12, -2)
    p.drawLine(-2, 2, 2, -2)
    p.drawLine(8, 12, 12, 8)
    p.end()
    return QBrush(pm)


class Timeline(QWidget):
    seekRequested = Signal(float)
    scrubStarted = Signal()
    scrubEnded = Signal()
    editsChanged = Signal()

    HEADER_W = 86
    RULER_H = 30
    TRACK_H = 64
    GAP = 6
    SB_H = 12

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.NoFocus)
        self.duration = 0.0
        self.fps = 30.0
        self.snap_fn = lambda t: t
        self.segments = []
        self.selected = None
        self.undo_stack, self.redo_stack = [], []
        self.playhead = 0.0
        self.pps = 100.0
        self.x0 = 0.0
        self.has_video = self.has_audio = False
        self.peaks = None
        self._pk = None
        self._pk_norm = 1.0
        self.audio_state = ""     # "", "loading", "error", "ready"
        self.thumbs = []
        self.title = ""
        self.hover_x = None
        self._drag = None
        self._hatch = _hatch_brush()
        self._layer = None
        self.sb = QScrollBar(Qt.Horizontal, self)
        self.sb.setFocusPolicy(Qt.NoFocus)
        self.sb.valueChanged.connect(self._on_scroll)
        self._update_height()

    # ------------------------------------------------------------------ setup
    def load(self, duration, fps, snap_fn, has_video, has_audio, title):
        self.duration = max(duration, 1e-3)
        self.fps = fps
        self.snap_fn = snap_fn
        self.has_video, self.has_audio = has_video, has_audio
        self.title = title
        self.segments = [[0.0, self.duration]]
        self.selected = None
        self.undo_stack, self.redo_stack = [], []
        self.playhead = 0.0
        self.peaks = self._pk = None
        self.audio_state = "loading" if has_audio else ""
        self.thumbs = []
        self._update_height()
        self.fit_view()
        self.editsChanged.emit()

    def set_peaks(self, peaks):
        self.peaks = peaks
        if peaks is not None and len(peaks):
            self._pk = np.concatenate([peaks, np.zeros(1, np.float32)])
            self._pk_norm = max(float(peaks.max()), 0.02)
            self.audio_state = "ready"
        else:
            self.audio_state = "error"
        self._invalidate()

    def set_thumbs(self, thumbs):
        # pre-scale once so painting never resamples
        h = int(self.TRACK_H * self.devicePixelRatioF())
        self.thumbs = [t.scaledToHeight(h, Qt.SmoothTransformation) if t is not None and t.height() != h else t
                       for t in thumbs]
        self._invalidate()

    def _update_height(self):
        tracks = int(self.has_video) + int(self.has_audio)
        self.setFixedHeight(self.RULER_H + self.GAP + max(1, tracks) * (self.TRACK_H + self.GAP) + self.SB_H + 4)

    # ------------------------------------------------------------------ view math
    def left(self):
        return self.HEADER_W + 10

    def right(self):
        return self.width() - 14

    def content_w(self):
        return max(10, self.right() - self.left())

    def tx(self, t):
        return self.left() + (t - self.x0) * self.pps

    def xt(self, x):
        return self.x0 + (x - self.left()) / self.pps

    def visible_secs(self):
        return self.content_w() / self.pps

    def min_pps(self):
        return self.content_w() / self.duration if self.duration else 1

    def max_pps(self):
        return max(self.min_pps(), self.fps * 60)

    def fit_view(self):
        self.pps = self.min_pps()
        self.x0 = 0.0
        self._sync_scrollbar()
        self._invalidate()

    def zoom(self, factor, anchor_x=None):
        if anchor_x is None:
            anchor_x = self.tx(self.playhead) if self.left() <= self.tx(self.playhead) <= self.right() else self.left() + self.content_w() / 2
        t = self.xt(anchor_x)
        self.pps = max(self.min_pps(), min(self.max_pps(), self.pps * factor))
        self.x0 = t - (anchor_x - self.left()) / self.pps
        self._clamp_x0()
        self._sync_scrollbar()
        self._invalidate()

    def _clamp_x0(self):
        self.x0 = max(0.0, min(self.x0, max(0.0, self.duration - self.visible_secs())))

    def _sync_scrollbar(self):
        self.sb.blockSignals(True)
        rng = max(0, int((self.duration - self.visible_secs()) * 1000))
        self.sb.setRange(0, rng)
        self.sb.setPageStep(max(1, int(self.visible_secs() * 1000)))
        self.sb.setSingleStep(max(1, int(self.visible_secs() * 50)))
        self.sb.setValue(int(self.x0 * 1000))
        self.sb.setVisible(rng > 0)
        self.sb.blockSignals(False)

    def _on_scroll(self, v):
        self.x0 = v / 1000.0
        self._invalidate()

    def resizeEvent(self, e):
        self.sb.setGeometry(self.left(), self.height() - self.SB_H - 2, self.content_w(), self.SB_H)
        fit = abs(self.pps - self.min_pps()) < 1e-6 or self.pps < self.min_pps()
        if self.duration:
            if fit or self.pps <= self.content_w() / self.duration * 1.0001:
                self.pps = self.min_pps()
            self._clamp_x0()
            self._sync_scrollbar()
        self._layer = None
        super().resizeEvent(e)

    def set_playhead(self, t, follow=False):
        old_x = self.tx(self.playhead)
        self.playhead = t
        if follow and self.duration:
            x = self.tx(t)
            if x > self.right() - 20 or x < self.left():
                self.x0 = t - self.visible_secs() * 0.08
                self._clamp_x0()
                self._sync_scrollbar()
                self._invalidate()
                return
        if abs(self.tx(t) - old_x) >= 0.5:
            self.update()

    def _invalidate(self):
        self._layer = None
        self.update()

    def track_rects(self):
        y = self.RULER_H + self.GAP
        out = {}
        if self.has_video:
            out["video"] = QRectF(self.left(), y, self.content_w(), self.TRACK_H)
            y += self.TRACK_H + self.GAP
        if self.has_audio:
            out["audio"] = QRectF(self.left(), y, self.content_w(), self.TRACK_H)
        return out

    # ------------------------------------------------------------------ edits
    def _push(self):
        self.undo_stack.append([list(s) for s in self.segments])
        if len(self.undo_stack) > 200:
            self.undo_stack.pop(0)
        self.redo_stack.clear()

    def _changed(self):
        self._invalidate()
        self.editsChanged.emit()

    @property
    def edited(self):
        return self.segments != [[0.0, self.duration]]

    def eps(self):
        return 0.5 / self.fps

    def split(self, t):
        t = self.snap_fn(t)
        for i, (s, e) in enumerate(self.segments):
            if s + self.eps() < t < e - self.eps():
                self._push()
                self.segments[i:i + 1] = [[s, t], [t, e]]
                self.selected = i + 1
                self._changed()
                return True
        return False

    def delete_selected(self):
        if self.selected is None or self.selected >= len(self.segments):
            return False
        self._push()
        del self.segments[self.selected]
        self.selected = None
        self._changed()
        return True

    def trim_before(self, t):
        t = self.snap_fn(t)
        new = [[max(s, t), e] for s, e in self.segments if e > t + self.eps()]
        if new != self.segments:
            self._push()
            self.segments = new
            self.selected = None
            self._changed()

    def trim_after(self, t):
        t = self.snap_fn(t)
        new = [[s, min(e, t)] for s, e in self.segments if s < t - self.eps()]
        if new != self.segments:
            self._push()
            self.segments = new
            self.selected = None
            self._changed()

    def keep_selected(self):
        if self.selected is None:
            return
        self._push()
        self.segments = [self.segments[self.selected]]
        self.selected = 0
        self._changed()

    def reset(self):
        if self.edited:
            self._push()
            self.segments = [[0.0, self.duration]]
            self.selected = None
            self._changed()

    def undo(self):
        if self.undo_stack:
            self.redo_stack.append([list(s) for s in self.segments])
            self.segments = self.undo_stack.pop()
            self.selected = None
            self._changed()
            return True
        return False

    def redo(self):
        if self.redo_stack:
            self.undo_stack.append([list(s) for s in self.segments])
            self.segments = self.redo_stack.pop()
            self.selected = None
            self._changed()
            return True
        return False

    def kept_duration(self):
        return sum(e - s for s, e in self.segments)

    def segment_at(self, t):
        for i, (s, e) in enumerate(self.segments):
            if s - 1e-6 <= t < e:
                return i
        return None

    def next_start(self, t):
        for s, e in self.segments:
            if s > t - 1e-6:
                return s
        return None

    def first_start(self):
        return self.segments[0][0] if self.segments else None

    def last_end(self):
        return self.segments[-1][1] if self.segments else None

    # ------------------------------------------------------------------ painting
    def paintEvent(self, e):
        dpr = self.devicePixelRatioF()
        if self._layer is None or self._layer.size() != self.size() * dpr:
            self._layer = QPixmap(self.size() * dpr)
            self._layer.setDevicePixelRatio(dpr)
            lp = QPainter(self._layer)
            self._paint_static(lp)
            lp.end()
        p = QPainter(self)
        p.drawPixmap(0, 0, self._layer)
        if not self.duration:
            return
        p.setClipRect(QRectF(self.left() - 2, 0, self.content_w() + 4, self.height()))
        self._paint_hover(p)
        self._paint_playhead(p)

    def _paint_static(self, p):
        p.fillRect(self.rect(), QColor(theme.PANEL))
        if not self.duration:
            return
        tracks = self.track_rects()
        self._paint_headers(p, tracks)
        p.save()
        p.setClipRect(QRectF(self.left() - 2, 0, self.content_w() + 4, self.height()))
        self._paint_ruler(p)
        if "video" in tracks:
            self._paint_video(p, tracks["video"])
        if "audio" in tracks:
            self._paint_audio(p, tracks["audio"])
        p.restore()

    def _paint_headers(self, p, tracks):
        p.setRenderHint(QPainter.Antialiasing)
        f = QFont("Segoe UI Variable Text")
        f.setPixelSize(11)
        f.setWeight(QFont.DemiBold)
        out = self.kept_duration()
        p.setFont(f)
        p.setPen(QColor(theme.MUTED))
        p.drawText(QRectF(10, 0, self.HEADER_W - 12, self.RULER_H), Qt.AlignVCenter | Qt.AlignLeft,
                   fmt_time(out, self.fps, True) if self.edited else "")
        for name, r in tracks.items():
            hr = QRectF(8, r.top(), self.HEADER_W - 12, r.height())
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(theme.SURFACE))
            p.drawRoundedRect(hr, 8, 8)
            glyph = theme.GLYPHS["image"] if name == "video" else theme.GLYPHS["volume"]
            p.setFont(theme.icon_font(16))
            p.setPen(QColor(theme.ACCENT if name == "video" else theme.WAVE))
            p.drawText(QRectF(hr.left() + 10, hr.top(), 20, hr.height()), Qt.AlignVCenter, glyph)
            p.setFont(f)
            p.setPen(QColor(theme.TEXT))
            p.drawText(QRectF(hr.left() + 36, hr.top(), hr.width() - 36, hr.height()), Qt.AlignVCenter,
                       "Video" if name == "video" else "Audio")

    def _tick_steps(self):
        fr = 1.0 / self.fps
        cands = sorted({fr, fr * 2, fr * 5, fr * 10, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200})
        major = next((c for c in cands if c * self.pps >= 80), cands[-1])
        if fr * self.pps >= 7 and major <= 2:
            minor = fr
        else:
            minor = major / 5 if major >= 5 or major < 1 else major / 2
            if minor * self.pps < 6:
                minor = major
        return major, minor

    def _paint_ruler(self, p):
        p.setRenderHint(QPainter.Antialiasing, False)
        major, minor = self._tick_steps()
        t0, t1 = self.xt(self.left()), self.xt(self.right())
        f = QFont("Segoe UI Variable Text")
        f.setPixelSize(10)
        p.setFont(f)
        y = self.RULER_H
        p.setPen(QPen(QColor(theme.BORDER), 1))
        p.drawLine(QPointF(self.left(), y - 0.5), QPointF(self.right(), y - 0.5))
        k0 = math.floor(t0 / minor)
        k1 = math.ceil(t1 / minor)
        ratio = max(1, int(round(major / minor)))
        for k in range(k0, k1 + 1):
            t = k * minor
            if t < 0 or t > self.duration + 1e-9:
                continue
            x = round(self.tx(t)) + 0.5
            is_major = k % ratio == 0
            if is_major:
                p.setPen(QPen(QColor("#4a4e58"), 1))
                p.drawLine(QPointF(x, y - 12), QPointF(x, y))
                p.setPen(QColor(theme.MUTED))
                p.drawText(QPointF(x + 4, y - 14), fmt_time(t, self.fps, frames=major < 1))
            else:
                p.setPen(QPen(QColor("#33363e"), 1))
                p.drawLine(QPointF(x, y - 5), QPointF(x, y))

    def _seg_rects(self, track):
        for i, (s, e) in enumerate(self.segments):
            x1, x2 = self.tx(s), self.tx(e)
            yield i, QRectF(x1, track.top(), max(1.0, x2 - x1), track.height())

    def _paint_removed(self, p, track):
        p.save()
        p.setRenderHint(QPainter.Antialiasing, False)
        p.setBrushOrigin(0, 0)
        full = QRectF(self.tx(0), track.top(), self.duration * self.pps, track.height())
        path = QPainterPath()
        path.addRect(full)
        for _, r in self._seg_rects(track):
            k = QPainterPath()
            k.addRect(r)
            path = path.subtracted(k)
        p.fillPath(path, self._hatch)
        p.restore()

    def _paint_video(self, p, track):
        self._paint_removed(p, track)
        p.setRenderHint(QPainter.Antialiasing)
        thumbs = self.thumbs
        n = len(thumbs)
        sample = next((t for t in thumbs if t is not None), None)
        tw = track.height() * (sample.width() / sample.height()) if sample is not None else track.height() * 16 / 9
        for i, r in self._seg_rects(track):
            if r.right() < self.left() or r.left() > self.right():
                continue
            clip = QPainterPath()
            clip.addRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 6, 6)
            p.save()
            p.setClipPath(clip, Qt.IntersectClip)
            p.fillRect(r, QColor(theme.SURFACE2))
            if n and sample is not None:
                origin = self.tx(0)
                k0 = int(math.floor((max(r.left(), self.left()) - origin) / tw))
                k1 = int(math.ceil((min(r.right(), self.right()) - origin) / tw))
                p.setRenderHint(QPainter.SmoothPixmapTransform)
                for k in range(k0, k1 + 1):
                    x = origin + k * tw
                    tm = self.xt(x + tw / 2)
                    idx = int(min(n - 1, max(0, tm / self.duration * n)))
                    img = thumbs[idx]
                    if img is not None:
                        p.drawImage(QRectF(x, r.top(), tw, r.height()), img)
            g = QLinearGradient(0, r.top(), 0, r.top() + 22)
            g.setColorAt(0, QColor(0, 0, 0, 150))
            g.setColorAt(1, QColor(0, 0, 0, 0))
            p.fillRect(QRectF(r.left(), r.top(), r.width(), 22), g)
            if r.width() > 60:
                f = QFont("Segoe UI Variable Text")
                f.setPixelSize(10)
                f.setWeight(QFont.DemiBold)
                p.setFont(f)
                p.setPen(QColor(255, 255, 255, 220))
                s, e = self.segments[i]
                p.drawText(QRectF(max(r.left(), self.left()) + 7, r.top() + 3, r.width() - 12, 14), Qt.AlignLeft,
                           f"{self.title}  ·  {fmt_time(e - s, self.fps, True)}")
            p.restore()
            self._paint_seg_border(p, r, i)

    def _paint_seg_border(self, p, r, i):
        sel = i == self.selected
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(theme.ACCENT) if sel else QColor(255, 255, 255, 40), 2 if sel else 1))
        p.drawRoundedRect(r.adjusted(1, 1, -1, -1) if sel else r.adjusted(0.5, 0.5, -0.5, -0.5), 6, 6)

    def _paint_audio(self, p, track):
        self._paint_removed(p, track)
        p.setRenderHint(QPainter.Antialiasing)
        for i, r in self._seg_rects(track):
            clip = QPainterPath()
            clip.addRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 6, 6)
            p.save()
            p.setClipPath(clip, Qt.IntersectClip)
            p.fillRect(r, QColor("#10302b"))
            p.restore()
        if self._pk is not None:
            x_start = max(self.left(), int(self.tx(0)))
            x_end = min(self.right(), int(self.tx(self.duration)) + 1)
            n = int(x_end - x_start)
            if n > 1:
                xs = np.arange(n + 1, dtype=np.float64) + x_start
                ts = self.x0 + (xs - self.left()) / self.pps
                b = np.clip((ts * AUDIO_RATE / PEAK_BLOCK).astype(np.int64), 0, len(self._pk) - 1)
                vals = np.maximum.reduceat(self._pk, b)[:-1] / self._pk_norm
                vals = np.clip(vals, 0, 1)
                mid = track.center().y()
                half = track.height() / 2 - 5
                top = [QPointF(x_start + i + 0.5, mid - max(0.5, v * half)) for i, v in enumerate(vals)]
                bot = [QPointF(x_start + i + 0.5, mid + max(0.5, v * half)) for i, v in reversed(list(enumerate(vals)))]
                poly = QPolygonF(top + bot)
                p.save()
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(80, 90, 100, 90))
                p.drawPolygon(poly)
                for _, r in self._seg_rects(track):
                    clip = QPainterPath()
                    clip.addRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 6, 6)
                    p.save()
                    p.setClipPath(clip, Qt.IntersectClip)
                    p.setBrush(QColor(theme.WAVE))
                    p.drawPolygon(poly)
                    p.restore()
                p.setPen(QPen(QColor(56, 209, 176, 80), 1))
                p.drawLine(QPointF(x_start, mid), QPointF(x_end, mid))
                p.restore()
        elif self.audio_state in ("loading", "error"):
            f = QFont("Segoe UI Variable Text")
            f.setPixelSize(11)
            p.setFont(f)
            p.setPen(QColor(theme.MUTED))
            p.drawText(track.adjusted(10, 0, 0, 0), Qt.AlignVCenter,
                       "Loading waveform…" if self.audio_state == "loading" else "Audio could not be decoded")
        for i, r in self._seg_rects(track):
            self._paint_seg_border(p, r, i)

    def _paint_hover(self, p):
        if self.hover_x is None or self._drag is not None:
            return
        x = self.hover_x
        if not self.left() <= x <= self.right():
            return
        p.setRenderHint(QPainter.Antialiasing, False)
        p.setPen(QPen(QColor(255, 255, 255, 50), 1))
        p.drawLine(QPointF(x + 0.5, self.RULER_H), QPointF(x + 0.5, self.height() - self.SB_H - 4))

    def _paint_playhead(self, p):
        x = self.tx(self.playhead)
        if x < self.left() - 10 or x > self.right() + 10:
            return
        p.setRenderHint(QPainter.Antialiasing)
        acc = QColor(theme.ACCENT)
        p.setPen(QPen(acc, 2))
        p.drawLine(QPointF(x, self.RULER_H - 4), QPointF(x, self.height() - self.SB_H - 4))
        # handle
        f = QFont("Segoe UI Variable Text")
        f.setPixelSize(10)
        f.setWeight(QFont.DemiBold)
        p.setFont(f)
        label = fmt_time(self.playhead, self.fps, True)
        w = p.fontMetrics().horizontalAdvance(label) + 14
        r = QRectF(x - w / 2, 3, w, 17)
        if r.left() < self.left() - 6:
            r.moveLeft(self.left() - 6)
        if r.right() > self.right() + 6:
            r.moveRight(self.right() + 6)
        path = QPainterPath()
        path.addRoundedRect(r, 5, 5)
        tri = QPainterPath()
        tri.addPolygon(QPolygonF([QPointF(x - 5, r.bottom() - 1), QPointF(x + 5, r.bottom() - 1), QPointF(x, r.bottom() + 6)]))
        p.setPen(Qt.NoPen)
        p.setBrush(acc)
        p.drawPath(path.united(tri))
        p.setPen(QColor("white"))
        p.drawText(r, Qt.AlignCenter, label)

    # ------------------------------------------------------------------ input
    def _edge_at(self, x, y):
        tracks = self.track_rects()
        if not any(r.top() <= y <= r.bottom() for r in tracks.values()):
            return None
        best = None
        for i, (s, e) in enumerate(self.segments):
            for side, t in (("l", s), ("r", e)):
                d = abs(self.tx(t) - x)
                if d <= 7:
                    # on a shared boundary, the side the cursor is on wins
                    inside = (x >= self.tx(t)) if side == "l" else (x <= self.tx(t))
                    score = d - (3 if inside else 0)
                    if best is None or score < best[0]:
                        best = (score, i, side)
        return (best[1], best[2]) if best else None

    def _t_at(self, x):
        return self.snap_fn(max(0.0, min(self.duration, self.xt(x))))

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton or not self.duration:
            return
        x, y = e.position().x(), e.position().y()
        if x < self.left() - 6:
            return
        edge = self._edge_at(x, y)
        if edge:
            self._push()
            self._drag = ("edge", edge[0], edge[1], [list(s) for s in self.segments])
            self.selected = edge[0]
            self.scrubStarted.emit()
            self._invalidate()
            return
        t = self._t_at(x)
        if y > self.RULER_H:
            self.selected = self.segment_at(self.xt(x))
        self._drag = ("scrub",)
        self.scrubStarted.emit()
        self.seekRequested.emit(t)
        self._invalidate()

    def mouseMoveEvent(self, e):
        x, y = e.position().x(), e.position().y()
        self.hover_x = x
        if self._drag is None:
            edge = self._edge_at(x, y) if self.duration else None
            self.setCursor(Qt.SizeHorCursor if edge else Qt.ArrowCursor)
            self.update()
            return
        # auto-scroll when dragging past the edges
        if x > self.right() and self.x0 < self.duration - self.visible_secs():
            self.x0 = min(self.x0 + (x - self.right()) / self.pps * 0.3, self.duration - self.visible_secs())
            self._sync_scrollbar()
        elif x < self.left() and self.x0 > 0:
            self.x0 = max(0.0, self.x0 - (self.left() - x) / self.pps * 0.3)
            self._sync_scrollbar()
        if self._drag[0] == "scrub":
            self.seekRequested.emit(self._t_at(x))
        elif self._drag[0] == "edge":
            _, i, side, before = self._drag
            t = self._t_at(x)
            fr = 1.0 / self.fps
            s, e_ = self.segments[i]
            if side == "l":
                lo = self.segments[i - 1][1] if i > 0 else 0.0
                t = max(lo, min(t, e_ - fr))
                self.segments[i][0] = t
                self.seekRequested.emit(t)
            else:
                hi = self.segments[i + 1][0] if i + 1 < len(self.segments) else self.duration
                t = max(s + fr, min(t, hi))
                self.segments[i][1] = t
                self.seekRequested.emit(max(s, t - fr))
            self._invalidate()

    def mouseReleaseEvent(self, e):
        if self._drag is None:
            return
        if self._drag[0] == "edge":
            if self.segments == self._drag[3]:
                self.undo_stack.pop()
            else:
                self.editsChanged.emit()
        self._drag = None
        self.scrubEnded.emit()
        self._invalidate()

    def leaveEvent(self, e):
        self.hover_x = None
        self.update()

    def wheelEvent(self, e):
        if not self.duration:
            return
        dx, dy = e.angleDelta().x(), e.angleDelta().y()
        if dy and not (e.modifiers() & Qt.ShiftModifier):
            self.zoom(1.25 ** (dy / 120), e.position().x())   # wheel zooms around the cursor
        else:
            d = dy or dx
            self.x0 -= d / 120 * self.visible_secs() * 0.1     # shift+wheel / trackpad scrolls
            self._clamp_x0()
            self._sync_scrollbar()
            self._invalidate()
