"""Zoomable/pannable canvas with crop and annotation tools.

All annotation and crop geometry is stored in *image* coordinates (source pixels), so it is
independent of zoom, and of the resolution the video decoder happens to deliver frames at.
"""
import math

from PySide6.QtCore import QPointF, QRectF, QSize, QSizeF, Qt, QTimer, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QImage, QPainter, QPainterPath,
                           QPainterPathStroker, QPen, QPixmap, QPolygonF, QTransform)
from PySide6.QtWidgets import QLineEdit, QWidget

from . import theme

DRAW_TOOLS = ("pen", "highlight", "arrow", "line", "rect", "ellipse", "text", "redact")


def _smooth_path(pts):
    path = QPainterPath(pts[0])
    if len(pts) < 3:
        for p in pts[1:]:
            path.lineTo(p)
        return path
    for i in range(1, len(pts) - 1):
        mid = (pts[i] + pts[i + 1]) / 2
        path.quadTo(pts[i], mid)
    path.lineTo(pts[-1])
    return path


class Annotation:
    def __init__(self, kind, pts, color, width, text=""):
        self.kind = kind
        self.pts = pts
        self.color = QColor(color)
        self.width = float(width)   # stroke width, or font pixel size for text (image coords)
        self.text = text
        self._cache = None

    def copy(self):
        return Annotation(self.kind, [QPointF(p) for p in self.pts], self.color, self.width, self.text)

    def mapped(self, fn):
        a = self.copy()
        a.pts = [fn(p) for p in a.pts]
        return a

    def rect(self):
        return QRectF(self.pts[0], self.pts[-1]).normalized()

    def text_path(self):
        f = QFont("Segoe UI Variable Display")
        f.setPixelSize(max(1, int(round(self.width))))
        f.setWeight(QFont.DemiBold)
        path = QPainterPath()
        ascent = self.width * 0.95
        for i, line in enumerate(self.text.split("\n")):
            path.addText(self.pts[0] + QPointF(0, ascent + i * self.width * 1.25), f, line)
        return path

    def shape(self):
        k = self.kind
        if k in ("pen", "highlight"):
            return _smooth_path(self.pts)
        if k in ("line", "arrow"):
            path = QPainterPath(self.pts[0])
            path.lineTo(self.pts[-1])
            return path
        if k == "rect" or k == "redact":
            path = QPainterPath()
            path.addRect(self.rect())
            return path
        if k == "ellipse":
            path = QPainterPath()
            path.addEllipse(self.rect())
            return path
        if k == "text":
            return self.text_path()
        return QPainterPath()

    def hit(self, pt, tol):
        if self.kind in ("redact", "text"):
            return self.shape().boundingRect().adjusted(-tol, -tol, tol, tol).contains(pt)
        st = QPainterPathStroker()
        st.setWidth(self.width + tol * 2)
        return st.createStroke(self.shape()).contains(pt)

    def paint(self, p: QPainter, base: QImage = None):
        k = self.kind
        if k == "redact":
            if base is None:
                return
            r = self.rect().intersected(QRectF(0, 0, base.width(), base.height())).toAlignedRect()
            if r.width() < 1 or r.height() < 1:
                return
            key = (r.x(), r.y(), r.width(), r.height(), base.cacheKey())
            if self._cache is None or self._cache[0] != key:
                block = max(6, int(max(r.width(), r.height()) / 14))
                small = base.copy(r).scaled(max(1, r.width() // block), max(1, r.height() // block),
                                            Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
                self._cache = (key, small)
            p.save()
            p.setRenderHint(QPainter.SmoothPixmapTransform, False)
            p.drawImage(QRectF(r), self._cache[1])
            p.restore()
            return
        if k == "text":
            path = self.text_path()
            p.save()
            p.setRenderHint(QPainter.Antialiasing)
            outline = QColor(0, 0, 0, 160) if self.color.lightness() > 90 else QColor(255, 255, 255, 170)
            p.strokePath(path, QPen(outline, max(1.0, self.width / 9), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.fillPath(path, self.color)
            p.restore()
            return
        col = QColor(self.color)
        if k == "highlight":
            col.setAlpha(105)
        pen = QPen(col, self.width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        if k in ("pen", "highlight"):
            if len(self.pts) == 1:
                p.drawPoint(self.pts[0])
            else:
                p.drawPath(_smooth_path(self.pts))
        elif k == "line":
            p.drawLine(self.pts[0], self.pts[-1])
        elif k == "arrow":
            a, b = self.pts[0], self.pts[-1]
            ang = math.atan2(b.y() - a.y(), b.x() - a.x())
            head = self.width * 4.2 + 4
            length = math.hypot(b.x() - a.x(), b.y() - a.y())
            if length > head * 0.6:
                end = b - QPointF(math.cos(ang), math.sin(ang)) * head * 0.6
                p.drawLine(a, end)
            spread = 0.42
            poly = QPolygonF([
                b,
                b - QPointF(math.cos(ang - spread), math.sin(ang - spread)) * head,
                b - QPointF(math.cos(ang + spread), math.sin(ang + spread)) * head,
            ])
            p.setPen(QPen(col, max(1.0, self.width * 0.5), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(col)
            p.drawPolygon(poly)
        elif k == "rect":
            p.drawRoundedRect(self.rect(), self.width * 0.6, self.width * 0.6)
        elif k == "ellipse":
            p.drawEllipse(self.rect())


def _checker_brush():
    pm = QPixmap(16, 16)
    pm.fill(QColor("#2a2c32"))
    p = QPainter(pm)
    p.fillRect(0, 0, 8, 8, QColor("#34373e"))
    p.fillRect(8, 8, 8, 8, QColor("#34373e"))
    p.end()
    return QBrush(pm)


class Canvas(QWidget):
    zoomChanged = Signal(float)
    cursorInfo = Signal(str)
    cropChanged = Signal()
    historyChanged = Signal()
    needsHiRes = Signal()
    toolChanged = Signal(str)
    openRequested = Signal()

    MIN_SCALE, MAX_SCALE = 0.02, 64.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAttribute(Qt.WA_OpaquePaintEvent)
        self.setMinimumSize(200, 150)
        self.image = None
        self.logical = QSizeF()
        self.scale = 1.0
        self.offset = QPointF()
        self.fit_mode = True
        self.mode = "empty"          # empty | image | video | audio
        self.placeholder_title = ""
        self.tool = "move"
        self.color = QColor("#ff4d5e")
        self.stroke = 5              # in screen pixels
        self.annotations = []
        self.undo_stack, self.redo_stack = [], []
        self.version = 0
        self.saved_version = 0
        self.current = None
        self.crop_rect = None
        self.crop_aspect = None
        self._crop_drag = None
        self._pan_last = None
        self._space = False
        self._erasing = False
        self._hover = None
        self.text_edit = None
        self._checker = _checker_brush()
        self._hires_timer = QTimer(self, singleShot=True, interval=140)
        self._hires_timer.timeout.connect(self.needsHiRes)

    # ------------------------------------------------------------------ content
    def set_image(self, img: QImage):
        self._cancel_text()
        if img.format() != QImage.Format_ARGB32_Premultiplied:
            img = img.convertToFormat(QImage.Format_ARGB32_Premultiplied)
        self.mode = "image"
        self.image = img
        self.logical = QSizeF(img.size())
        self.annotations, self.undo_stack, self.redo_stack = [], [], []
        self.version = self.saved_version = 0
        self.crop_rect = None
        self.fit()
        self.historyChanged.emit()
        self.cropChanged.emit()

    def set_frame(self, img: QImage, logical: QSize):
        """Show a video frame. `logical` is the source resolution; img may be smaller."""
        relayout = self.mode != "video" or QSizeF(logical) != self.logical
        self.mode = "video"
        self.image = img
        self.logical = QSizeF(logical)
        if relayout:
            self.annotations, self.undo_stack, self.redo_stack = [], [], []
            self.crop_rect = None
            self.fit()
            self.cropChanged.emit()
        self.update()

    def set_placeholder(self, mode, title=""):
        self._cancel_text()
        self.mode = mode
        self.placeholder_title = title
        self.image = None
        self.logical = QSizeF()
        self.annotations, self.undo_stack, self.redo_stack = [], [], []
        self.crop_rect = None
        self.update()
        self.historyChanged.emit()
        self.cropChanged.emit()

    @property
    def dirty(self):
        return self.mode == "image" and self.version != self.saved_version

    def has_content(self):
        return self.image is not None and not self.logical.isEmpty()

    # ------------------------------------------------------------------ view math
    def dpr(self):
        return self.devicePixelRatioF()

    def to_widget(self, p: QPointF) -> QPointF:
        return self.offset + p * self.scale

    def to_image(self, p: QPointF) -> QPointF:
        return (p - self.offset) / self.scale

    def image_rect_widget(self) -> QRectF:
        return QRectF(self.offset, self.logical * self.scale)

    def fit(self):
        if not self.has_content():
            return
        margin = 24 if self.mode == "image" else 0
        w, h = self.width() - margin * 2, self.height() - margin * 2
        s = min(w / self.logical.width(), h / self.logical.height())
        if self.mode == "image":
            s = min(s, 1.0 / self.dpr())   # don't blow small images up beyond 100%
        self.scale = max(self.MIN_SCALE, s)
        self._center()
        self.fit_mode = True
        self._after_zoom()

    def _center(self):
        size = self.logical * self.scale
        self.offset = QPointF((self.width() - size.width()) / 2, (self.height() - size.height()) / 2)

    def zoom_to(self, scale, anchor: QPointF = None):
        if not self.has_content():
            return
        scale = max(self.MIN_SCALE, min(self.MAX_SCALE, scale))
        if anchor is None:
            anchor = QPointF(self.width() / 2, self.height() / 2)
        ip = self.to_image(anchor)
        self.scale = scale
        self.offset = anchor - ip * scale
        self.fit_mode = False
        self._clamp_offset()
        self._after_zoom()

    def zoom_step(self, steps, anchor=None):
        self.zoom_to(self.scale * (1.2 ** steps), anchor)

    def actual_size(self):
        self.zoom_to(1.0 / self.dpr())

    def zoom_percent(self):
        return self.scale * self.dpr() * 100

    def _clamp_offset(self):
        # keep at least part of the image visible
        size = self.logical * self.scale
        m = 60
        x = min(max(self.offset.x(), m - size.width()), self.width() - m)
        y = min(max(self.offset.y(), m - size.height()), self.height() - m)
        if size.width() <= self.width():
            x = (self.width() - size.width()) / 2 if self.fit_mode else x
        self.offset = QPointF(x, y)

    def _after_zoom(self):
        self.zoomChanged.emit(self.zoom_percent())
        if self.mode == "video":
            self._hires_timer.start()
        self.update()

    def display_hint(self) -> QSize:
        """Device-pixel size the current frame should be decoded at."""
        if self.logical.isEmpty():
            return QSize(1920, 1080)
        f = min(1.0, self.scale * self.dpr())
        return QSize(max(16, int(self.logical.width() * f)), max(16, int(self.logical.height() * f)))

    def frame_too_small(self):
        if self.mode != "video" or self.image is None:
            return False
        return self.image.width() < self.display_hint().width() * 0.9

    def resizeEvent(self, e):
        if self.fit_mode:
            self.fit()
        else:
            self._clamp_offset()
        if self.text_edit:
            self._cancel_text()
        super().resizeEvent(e)

    # ------------------------------------------------------------------ painting
    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(theme.BG))
        if not self.has_content():
            self._paint_placeholder(p)
            return
        target = self.image_rect_widget()
        if self.image.hasAlphaChannel():
            p.save()
            p.setBrushOrigin(target.topLeft())
            p.fillRect(target, self._checker)
            p.restore()
        dpr = self.dpr()
        crisp = self.scale * dpr >= 3
        img = self.image
        if (self.mode == "video" and abs(img.width() - target.width() * dpr) <= 2.5
                and abs(img.height() - target.height() * dpr) <= 2.5):
            # decoder already delivered the frame at display size: blit 1:1, no resampling
            img.setDevicePixelRatio(dpr)
            p.drawImage(QPointF(round(target.x() * dpr) / dpr, round(target.y() * dpr) / dpr), img)
        else:
            p.setRenderHint(QPainter.SmoothPixmapTransform, not crisp)
            p.drawImage(target, img)
        if crisp and self.scale * self.dpr() >= 14:
            self._paint_pixel_grid(p, target)

        if self.annotations or self.current:
            p.save()
            p.setRenderHint(QPainter.Antialiasing)
            p.setClipRect(target)
            p.translate(self.offset)
            p.scale(self.scale, self.scale)
            base = self.image if self.mode == "image" else None
            for a in self.annotations:
                a.paint(p, base)
            if self.current:
                self.current.paint(p, base)
            p.restore()

        if self.crop_rect is not None:
            self._paint_crop(p)
        if self.tool == "eraser" and self._hover is not None and self.mode == "image":
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(QPen(QColor(255, 255, 255, 200), 1))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(self._hover, 8, 8)

    def _paint_pixel_grid(self, p, target):
        p.save()
        p.setPen(QPen(QColor(0, 0, 0, 60), 0))
        vis = target.intersected(QRectF(self.rect()))
        x0 = math.floor(self.to_image(vis.topLeft()).x())
        x1 = math.ceil(self.to_image(vis.bottomRight()).x())
        y0 = math.floor(self.to_image(vis.topLeft()).y())
        y1 = math.ceil(self.to_image(vis.bottomRight()).y())
        for x in range(x0, x1 + 1):
            wx = self.offset.x() + x * self.scale
            p.drawLine(QPointF(wx, vis.top()), QPointF(wx, vis.bottom()))
        for y in range(y0, y1 + 1):
            wy = self.offset.y() + y * self.scale
            p.drawLine(QPointF(vis.left(), wy), QPointF(vis.right(), wy))
        p.restore()

    def _paint_placeholder(self, p):
        p.setRenderHint(QPainter.Antialiasing)
        c = QPointF(self.width() / 2, self.height() / 2 - 30)
        if self.mode == "audio":
            glyph, title, sub = "", self.placeholder_title or "Audio", "Audio track — use the timeline below"
        else:
            glyph, title, sub = "", "Drop an image, GIF or video here", "or press Ctrl+O to browse"
        box = QRectF(c.x() - 48, c.y() - 70, 96, 96)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.SURFACE))
        p.drawRoundedRect(box, 24, 24)
        p.setFont(theme.icon_font(44))
        p.setPen(QColor(theme.ACCENT))
        p.drawText(box, Qt.AlignCenter, glyph)
        f = QFont("Segoe UI Variable Display")
        f.setPixelSize(20)
        f.setWeight(QFont.DemiBold)
        p.setFont(f)
        p.setPen(QColor(theme.TEXT))
        p.drawText(QRectF(0, c.y() + 40, self.width(), 30), Qt.AlignHCenter, title)
        f.setPixelSize(13)
        f.setWeight(QFont.Normal)
        p.setFont(f)
        p.setPen(QColor(theme.MUTED))
        p.drawText(QRectF(0, c.y() + 72, self.width(), 24), Qt.AlignHCenter, sub)

    def _crop_widget_rect(self):
        return QRectF(self.to_widget(self.crop_rect.topLeft()), self.crop_rect.size() * self.scale)

    def _paint_crop(self, p):
        r = self._crop_widget_rect()
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        outside = QPainterPath()
        outside.addRect(self.image_rect_widget().intersected(QRectF(self.rect())).adjusted(-1, -1, 1, 1))
        inner = QPainterPath()
        inner.addRect(r)
        p.fillPath(outside.subtracted(inner), QColor(0, 0, 0, 155))
        active = self.tool == "crop"
        if active:
            p.setPen(QPen(QColor(255, 255, 255, 70), 1))
            for i in (1, 2):
                x = r.left() + r.width() * i / 3
                y = r.top() + r.height() * i / 3
                p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
                p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
        p.setPen(QPen(QColor(255, 255, 255, 230) if active else QColor(theme.ACCENT), 1.2))
        p.setBrush(Qt.NoBrush)
        p.drawRect(r)
        if active:
            L = min(18, r.width() / 3, r.height() / 3)
            pen = QPen(QColor("white"), 3.5, Qt.SolidLine, Qt.FlatCap)
            p.setPen(pen)
            for cx, cy, sx, sy in ((r.left(), r.top(), 1, 1), (r.right(), r.top(), -1, 1),
                                   (r.left(), r.bottom(), 1, -1), (r.right(), r.bottom(), -1, -1)):
                p.drawLine(QPointF(cx, cy), QPointF(cx + sx * L, cy))
                p.drawLine(QPointF(cx, cy), QPointF(cx, cy + sy * L))
            for mx, my, horiz in ((r.center().x(), r.top(), True), (r.center().x(), r.bottom(), True),
                                  (r.left(), r.center().y(), False), (r.right(), r.center().y(), False)):
                if horiz:
                    p.drawLine(QPointF(mx - L / 2, my), QPointF(mx + L / 2, my))
                else:
                    p.drawLine(QPointF(mx, my - L / 2), QPointF(mx, my + L / 2))
        # size pill
        cr = self.crop_rect.toAlignedRect()
        label = f"{cr.width()} × {cr.height()}"
        f = QFont("Segoe UI Variable Text")
        f.setPixelSize(12)
        f.setWeight(QFont.DemiBold)
        p.setFont(f)
        tw = p.fontMetrics().horizontalAdvance(label) + 18
        pill = QRectF(r.center().x() - tw / 2, r.top() - 30, tw, 22)
        if pill.top() < 4:
            pill.moveTop(r.top() + 8)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(20, 21, 25, 220))
        p.drawRoundedRect(pill, 11, 11)
        p.setPen(QColor("white"))
        p.drawText(pill, Qt.AlignCenter, label)
        p.restore()

    # ------------------------------------------------------------------ tools
    def set_tool(self, tool):
        self._cancel_text()
        if tool == "crop" and self.has_content() and self.crop_rect is None:
            self.crop_rect = QRectF(0, 0, self.logical.width(), self.logical.height())
            if self.crop_aspect:
                self._apply_aspect()
            self.cropChanged.emit()
        if self.tool == "crop" and tool != "crop" and self.mode == "image":
            # leaving crop without applying discards the proposal
            self.crop_rect = None
            self.cropChanged.emit()
        self.tool = tool
        self._update_cursor()
        self.toolChanged.emit(tool)
        self.update()

    def _update_cursor(self, pos=None):
        if self._space or self.tool == "move":
            self.setCursor(Qt.ClosedHandCursor if self._pan_last else Qt.OpenHandCursor)
        elif self.tool == "crop":
            h = self._crop_handle_at(pos) if pos is not None else None
            cur = {"tl": Qt.SizeFDiagCursor, "br": Qt.SizeFDiagCursor, "tr": Qt.SizeBDiagCursor,
                   "bl": Qt.SizeBDiagCursor, "l": Qt.SizeHorCursor, "r": Qt.SizeHorCursor,
                   "t": Qt.SizeVerCursor, "b": Qt.SizeVerCursor, "move": Qt.SizeAllCursor}.get(h, Qt.CrossCursor)
            self.setCursor(cur)
        elif self.tool == "text":
            self.setCursor(Qt.IBeamCursor)
        elif self.tool == "eraser":
            self.setCursor(Qt.BlankCursor if self._hover is not None else Qt.ArrowCursor)
        else:
            self.setCursor(Qt.CrossCursor)

    def set_crop_aspect(self, aspect):
        self.crop_aspect = aspect
        if self.crop_rect is not None and aspect:
            self._apply_aspect()
            self.cropChanged.emit()
            self.update()

    def _apply_aspect(self):
        r = self.crop_rect
        a = self.crop_aspect
        c = r.center()
        w, h = r.width(), r.height()
        if w / max(h, 1e-6) > a:
            w = h * a
        else:
            h = w / a
        nr = QRectF(c.x() - w / 2, c.y() - h / 2, w, h)
        bounds = QRectF(0, 0, self.logical.width(), self.logical.height())
        if nr.left() < 0: nr.moveLeft(0)
        if nr.top() < 0: nr.moveTop(0)
        if nr.right() > bounds.right(): nr.moveRight(bounds.right())
        if nr.bottom() > bounds.bottom(): nr.moveBottom(bounds.bottom())
        self.crop_rect = nr

    def clear_crop(self):
        self.crop_rect = None
        self.cropChanged.emit()
        self.update()

    def crop_pixels(self):
        """Crop rectangle in source pixels, or None if it covers everything."""
        if self.crop_rect is None:
            return None
        r = self.crop_rect.toAlignedRect().intersected(QRectF(0, 0, self.logical.width(), self.logical.height()).toAlignedRect())
        if r.width() < 2 or r.height() < 2:
            return None
        if r.width() >= self.logical.width() and r.height() >= self.logical.height():
            return None
        return r

    def _crop_handle_at(self, pos):
        if self.crop_rect is None or pos is None:
            return None
        r = self._crop_widget_rect()
        tol = 11
        x, y = pos.x(), pos.y()
        near_l, near_r = abs(x - r.left()) < tol, abs(x - r.right()) < tol
        near_t, near_b = abs(y - r.top()) < tol, abs(y - r.bottom()) < tol
        in_x = r.left() - tol < x < r.right() + tol
        in_y = r.top() - tol < y < r.bottom() + tol
        if near_t and near_l: return "tl"
        if near_t and near_r: return "tr"
        if near_b and near_l: return "bl"
        if near_b and near_r: return "br"
        if near_l and in_y: return "l"
        if near_r and in_y: return "r"
        if near_t and in_x: return "t"
        if near_b and in_x: return "b"
        if r.contains(pos): return "move"
        return None

    def _clamp_img(self, ip):
        return QPointF(min(max(ip.x(), 0), self.logical.width()), min(max(ip.y(), 0), self.logical.height()))

    def _crop_drag_to(self, pos):
        h, start_rect, start_ip = self._crop_drag
        ip = self.to_image(pos)
        W, H = self.logical.width(), self.logical.height()
        if h == "move":
            d = ip - start_ip
            r = start_rect.translated(d)
            r.moveLeft(min(max(r.left(), 0), W - r.width()))
            r.moveTop(min(max(r.top(), 0), H - r.height()))
            self.crop_rect = r
            return
        ip = self._clamp_img(ip)
        l, t, rr, b = start_rect.left(), start_rect.top(), start_rect.right(), start_rect.bottom()
        a = self.crop_aspect
        if h in ("tl", "tr", "bl", "br"):
            ax = rr if "l" in h else l
            ay = b if "t" in h else t
            dx, dy = ip.x() - ax, ip.y() - ay
            w, hh = abs(dx), abs(dy)
            if a:
                sx = 1 if dx >= 0 else -1
                sy = 1 if dy >= 0 else -1
                if w / a > hh:
                    hh = w / a
                else:
                    w = hh * a
                maxw = (W - ax) if sx > 0 else ax
                maxh = (H - ay) if sy > 0 else ay
                w = min(w, maxw, maxh * a)
                hh = w / a
                dx, dy = sx * w, sy * hh
            self.crop_rect = QRectF(QPointF(ax, ay), QPointF(ax + dx, ay + dy)).normalized()
            return
        if h == "l": l = ip.x()
        if h == "r": rr = ip.x()
        if h == "t": t = ip.y()
        if h == "b": b = ip.y()
        r = QRectF(QPointF(l, t), QPointF(rr, b)).normalized()
        if a:
            c = start_rect.center()
            if h in ("l", "r"):
                nh = r.width() / a
                r = QRectF(r.left(), c.y() - nh / 2, r.width(), nh)
            else:
                nw = r.height() * a
                r = QRectF(c.x() - nw / 2, r.top(), nw, r.height())
            r = r.intersected(QRectF(0, 0, W, H))
        self.crop_rect = r

    def apply_crop(self):
        r = self.crop_pixels()
        if self.mode != "image":
            return
        if r is None:
            self.clear_crop()
            return
        self.push_undo()
        self.image = self.image.copy(r)
        off = QPointF(r.topLeft())
        self.annotations = [a.mapped(lambda p, o=off: p - o) for a in self.annotations]
        self.logical = QSizeF(self.image.size())
        self.crop_rect = None
        self.fit()
        self.cropChanged.emit()
        self._changed()

    def rotate(self, clockwise=True):
        if self.mode != "image":
            return
        self.push_undo()
        w, h = self.logical.width(), self.logical.height()
        self.image = self.image.transformed(QTransform().rotate(90 if clockwise else -90))
        if clockwise:
            fn = lambda p: QPointF(h - p.y(), p.x())
        else:
            fn = lambda p: QPointF(p.y(), w - p.x())
        self.annotations = [a.mapped(fn) for a in self.annotations]
        self.logical = QSizeF(self.image.size())
        self.crop_rect = None
        self.fit()
        self._changed()

    def flip(self, horizontal=True):
        if self.mode != "image":
            return
        self.push_undo()
        w, h = self.logical.width(), self.logical.height()
        self.image = self.image.flipped(Qt.Horizontal if horizontal else Qt.Vertical)
        fn = (lambda p: QPointF(w - p.x(), p.y())) if horizontal else (lambda p: QPointF(p.x(), h - p.y()))
        self.annotations = [a.mapped(fn) for a in self.annotations]
        self.crop_rect = None
        self.update()
        self._changed()

    # ------------------------------------------------------------------ history
    def push_undo(self):
        self.undo_stack.append((self.image, [a.copy() for a in self.annotations]))
        if len(self.undo_stack) > 60:
            self.undo_stack.pop(0)
        self.redo_stack.clear()

    def _changed(self):
        self.version += 1
        self.historyChanged.emit()
        self.update()

    def _restore(self, state):
        size_changed = state[0].size() != self.image.size()
        self.image, self.annotations = state[0], state[1]
        self.logical = QSizeF(self.image.size())
        if size_changed:
            self.fit()
        self.version += 1
        self.historyChanged.emit()
        self.update()

    def undo(self):
        if self.text_edit:
            self._cancel_text()
            return
        if self.undo_stack:
            self.redo_stack.append((self.image, [a.copy() for a in self.annotations]))
            self._restore(self.undo_stack.pop())

    def redo(self):
        if self.redo_stack:
            self.undo_stack.append((self.image, [a.copy() for a in self.annotations]))
            self._restore(self.redo_stack.pop())

    def mark_saved(self):
        self.saved_version = self.version

    def render_result(self, flatten_bg=None) -> QImage:
        out = QImage(self.image.size(), QImage.Format_ARGB32_Premultiplied)
        out.fill(QColor(flatten_bg) if flatten_bg else Qt.transparent)
        p = QPainter(out)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.drawImage(0, 0, self.image)
        for a in self.annotations:
            a.paint(p, self.image)
        p.end()
        return out

    # ------------------------------------------------------------------ input
    def wheelEvent(self, e):
        if not self.has_content():
            return
        d = e.angleDelta().y() / 120.0
        if d == 0:
            return
        self.zoom_step(d, e.position())

    def mouseDoubleClickEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        if not self.has_content():
            self.openRequested.emit()
            return
        if self.tool in ("move", "crop") and not self._crop_handle_at(e.position()):
            if self.fit_mode:
                self.zoom_to(1.0 / self.dpr(), e.position())
            else:
                self.fit()

    def mousePressEvent(self, e):
        self.setFocus()
        pos = e.position()
        if not self.has_content():
            if e.button() == Qt.LeftButton:
                self.openRequested.emit()
            return
        if e.button() == Qt.MiddleButton or (e.button() == Qt.LeftButton and (self._space or self.tool == "move")):
            self._pan_last = pos
            self._update_cursor()
            return
        if e.button() != Qt.LeftButton:
            return
        ip = self.to_image(pos)
        if self.tool == "crop":
            h = self._crop_handle_at(pos)
            if h is None:
                start = self._clamp_img(ip)
                self.crop_rect = QRectF(start, start)
                self._crop_drag = ("br", QRectF(start, start), start)
            else:
                self._crop_drag = (h, QRectF(self.crop_rect), ip)
            return
        if self.mode != "image":
            return
        if self.tool == "eraser":
            self._erasing = False
            self._erase_at(ip)
            return
        if self.tool == "text":
            self._begin_text(pos)
            return
        if self.tool in DRAW_TOOLS:
            w = self.stroke / self.scale
            if self.tool == "highlight":
                w *= 3.5
            self.current = Annotation(self.tool, [ip, QPointF(ip)], self.color, w)
            if self.tool in ("pen", "highlight"):
                self.current.pts = [ip]
            self.update()

    def mouseMoveEvent(self, e):
        pos = e.position()
        if self._pan_last is not None:
            self.offset += pos - self._pan_last
            self._pan_last = pos
            self.fit_mode = False
            self.update()
            return
        if not self.has_content():
            return
        ip = self.to_image(pos)
        self._emit_cursor(ip)
        if self.tool == "eraser":
            self._hover = pos
            if e.buttons() & Qt.LeftButton:
                self._erase_at(ip)
            self._update_cursor(pos)
            self.update()
            return
        if self._crop_drag is not None:
            self._crop_drag_to(pos)
            self.cropChanged.emit()
            self.update()
            return
        if self.current is not None:
            if self.current.kind in ("pen", "highlight"):
                last = self.current.pts[-1]
                if (ip - last).manhattanLength() * self.scale >= 2:
                    self.current.pts.append(ip)
            else:
                a = self.current.pts[0]
                if e.modifiers() & Qt.ShiftModifier:
                    ip = self._constrain(a, ip, self.current.kind)
                self.current.pts[-1] = ip
            self.update()
            return
        self._update_cursor(pos)

    def _constrain(self, a, b, kind):
        d = b - a
        if kind in ("rect", "ellipse", "redact"):
            m = max(abs(d.x()), abs(d.y()))
            return a + QPointF(math.copysign(m, d.x() or 1), math.copysign(m, d.y() or 1))
        ang = math.atan2(d.y(), d.x())
        snap = round(ang / (math.pi / 4)) * (math.pi / 4)
        length = math.hypot(d.x(), d.y())
        return a + QPointF(math.cos(snap), math.sin(snap)) * length

    def mouseReleaseEvent(self, e):
        if self._pan_last is not None and e.button() in (Qt.MiddleButton, Qt.LeftButton):
            self._pan_last = None
            self._update_cursor(e.position())
            return
        if self._crop_drag is not None:
            self._crop_drag = None
            if self.crop_rect is not None and (self.crop_rect.width() < 2 or self.crop_rect.height() < 2):
                self.crop_rect = QRectF(0, 0, self.logical.width(), self.logical.height())
            self.cropChanged.emit()
            self.update()
            return
        if self._erasing:
            self._erasing = False
        if self.current is not None:
            a = self.current
            self.current = None
            r = a.shape().boundingRect()
            tiny = max(r.width(), r.height()) * self.scale < 3
            if not (tiny and a.kind not in ("pen", "highlight")):
                self.push_undo()
                self.annotations.append(a)
                self._changed()
            self.update()

    def leaveEvent(self, e):
        self._hover = None
        self.cursorInfo.emit("")
        self.update()

    def _erase_at(self, ip):
        tol = 8 / self.scale
        for i in range(len(self.annotations) - 1, -1, -1):
            if self.annotations[i].hit(ip, tol):
                if not self._erasing:
                    self.push_undo()
                    self._erasing = True
                del self.annotations[i]
                self._changed()
                return

    def _emit_cursor(self, ip):
        x, y = int(math.floor(ip.x())), int(math.floor(ip.y()))
        if 0 <= x < self.logical.width() and 0 <= y < self.logical.height():
            fx = self.image.width() / self.logical.width()
            fy = self.image.height() / self.logical.height()
            c = self.image.pixelColor(min(self.image.width() - 1, int(x * fx)), min(self.image.height() - 1, int(y * fy)))
            self.cursorInfo.emit(f"{x}, {y}   ·   {c.name().upper()}  rgb({c.red()}, {c.green()}, {c.blue()})")
        else:
            self.cursorInfo.emit("")

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Space and not e.isAutoRepeat() and self.mode == "image":
            self._space = True
            self._update_cursor()
            return
        super().keyPressEvent(e)

    def keyReleaseEvent(self, e):
        if e.key() == Qt.Key_Space and not e.isAutoRepeat():
            self._space = False
            self._update_cursor()
            return
        super().keyReleaseEvent(e)

    # ------------------------------------------------------------------ text
    def _begin_text(self, pos):
        self._cancel_text()
        px = max(14, int(self.stroke * 4 + 10))
        ed = QLineEdit(self)
        ed.setFrame(False)
        f = QFont("Segoe UI Variable Display")
        f.setPixelSize(px)
        f.setWeight(QFont.DemiBold)
        ed.setFont(f)
        ed.setStyleSheet(
            f"QLineEdit {{ background: rgba(0,0,0,90); color: {self.color.name()}; border: 1px dashed rgba(255,255,255,140);"
            f" border-radius: 4px; padding: 0 3px; font-family: 'Segoe UI Variable Display'; font-size: {px}px;"
            f" font-weight: 600; }}")
        ed.setPlaceholderText("Type text, Enter to place")
        ed.setMinimumWidth(220)
        ed.adjustSize()
        ed.move(int(pos.x()) - 4, int(pos.y()) - 2)
        ed.show()
        ed.setFocus()
        ed._anchor = self.to_image(pos)
        ed._px = px
        ed.returnPressed.connect(self._commit_text)
        ed.editingFinished.connect(self._commit_text)
        ed.textChanged.connect(lambda _t, e=ed: e.resize(max(220, e.fontMetrics().horizontalAdvance(e.text()) + 30), e.height()))
        self.text_edit = ed

    def _commit_text(self):
        ed = self.text_edit
        if ed is None:
            return
        self.text_edit = None
        ed.hide()
        text = ed.text().strip()
        if text:
            self.push_undo()
            self.annotations.append(Annotation("text", [ed._anchor], self.color, ed._px / self.scale, text))
            self._changed()
        ed.deleteLater()
        self.setFocus()

    def _cancel_text(self):
        ed = self.text_edit
        if ed is not None:
            self.text_edit = None
            ed.blockSignals(True)
            ed.hide()
            ed.deleteLater()
            self.update()
