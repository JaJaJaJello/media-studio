"""Colors, stylesheet and icon helpers (icons are drawn from the Segoe Fluent Icons font)."""
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPixmap

ACCENT = "#5b8cff"
ACCENT_HOVER = "#7aa2ff"
ACCENT_DIM = "#2b3f73"
BG = "#0e0f12"
PANEL = "#16171b"
SURFACE = "#1d1f24"
SURFACE2 = "#262930"
BORDER = "#2b2e36"
TEXT = "#e7e8ec"
MUTED = "#8d909a"
DANGER = "#ff5d6c"
WAVE = "#38d1b0"

GLYPHS = {
    "open": "", "save": "", "copy": "", "play": "", "pause": "",
    "start": "", "end": "", "stepback": "", "stepfwd": "",
    "zoomin": "", "zoomout": "", "fit": "", "fullscreen": "",
    "move": "", "crop": "", "pen": "", "highlight": "",
    "arrow": "", "line": "", "rect": "", "ellipse": "",
    "text": "", "redact": "", "eraser": "",
    "undo": "", "redo": "", "rotate": "", "flip": "",
    "cut": "", "delete": "", "volume": "", "mute": "",
    "camera": "", "prev": "", "next": "", "loop": "",
    "settings": "", "export": "", "check": "", "close": "",
    "more": "", "image": "", "trimleft": "", "trimright": "",
}

_icon_font = None


def icon_font(px: int) -> QFont:
    global _icon_font
    if _icon_font is None:
        fams = QFontDatabase.families()
        _icon_font = "Segoe Fluent Icons" if "Segoe Fluent Icons" in fams else "Segoe MDL2 Assets"
    f = QFont(_icon_font)
    f.setPixelSize(px)
    return f


def glyph_pixmap(glyph: str, size: int, color: str, scale: int = 2) -> QPixmap:
    pm = QPixmap(size * scale, size * scale)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)
    p.setFont(icon_font(int(size * scale * 0.82)))
    p.setPen(QColor(color))
    p.drawText(QRectF(0, 0, size * scale, size * scale), Qt.AlignCenter, glyph)
    p.end()
    pm.setDevicePixelRatio(scale)
    return pm


_cache = {}


def icon(name: str, size: int = 18, color: str = TEXT) -> QIcon:
    key = (name, size, color)
    if key in _cache:
        return _cache[key]
    g = GLYPHS.get(name, name)
    ic = QIcon()
    ic.addPixmap(glyph_pixmap(g, size, color), QIcon.Normal, QIcon.Off)
    ic.addPixmap(glyph_pixmap(g, size, "#4a4d56"), QIcon.Disabled, QIcon.Off)
    ic.addPixmap(glyph_pixmap(g, size, "#ffffff"), QIcon.Active, QIcon.Off)
    ic.addPixmap(glyph_pixmap(g, size, "#ffffff"), QIcon.Normal, QIcon.On)
    ic.addPixmap(glyph_pixmap(g, size, "#ffffff"), QIcon.Active, QIcon.On)
    _cache[key] = ic
    return ic


STYLESHEET = f"""
* {{
    font-family: "Segoe UI Variable Text", "Segoe UI";
    font-size: 10pt;
    color: {TEXT};
    outline: none;
}}
QMainWindow, #Root {{ background: {BG}; }}
#TopBar, #OptionsBar, #Transport, #StatusBar {{ background: {PANEL}; }}
#TopBar {{ border-bottom: 1px solid {BORDER}; }}
#OptionsBar {{ border-bottom: 1px solid {BORDER}; }}
#Transport {{ border-top: 1px solid {BORDER}; }}
#StatusBar {{ border-top: 1px solid {BORDER}; }}
#StatusBar QLabel {{ color: {MUTED}; font-size: 9pt; }}
#ToolRail {{ background: {PANEL}; border-right: 1px solid {BORDER}; }}
#FileName {{ font-size: 10.5pt; font-weight: 600; }}
#FileMeta {{ color: {MUTED}; font-size: 9pt; }}
#TimeLabel {{ font-family: "Cascadia Mono", "Consolas"; font-size: 10.5pt; color: {TEXT}; }}
#FrameLabel {{ font-family: "Cascadia Mono", "Consolas"; font-size: 9pt; color: {MUTED}; }}
#Muted {{ color: {MUTED}; }}

QToolButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 7px;
    padding: 5px;
}}
QToolButton:hover {{ background: {SURFACE2}; }}
QToolButton:pressed {{ background: {BORDER}; }}
QToolButton:checked {{ background: {ACCENT_DIM}; border: 1px solid {ACCENT}; }}
QToolButton:disabled {{ background: transparent; }}
QToolButton::menu-indicator {{ image: none; width: 0; }}
QToolButton#PlayButton {{
    background: {ACCENT}; border-radius: 18px; padding: 8px;
}}
QToolButton#PlayButton:hover {{ background: {ACCENT_HOVER}; }}

QPushButton {{
    background: {SURFACE2};
    border: 1px solid {BORDER};
    border-radius: 7px;
    padding: 6px 14px;
}}
QPushButton:hover {{ background: #30333b; }}
QPushButton:pressed {{ background: {BORDER}; }}
QPushButton:disabled {{ color: #5a5d66; }}
QPushButton#Primary {{ background: {ACCENT}; border: 1px solid {ACCENT}; color: white; font-weight: 600; }}
QPushButton#Primary:hover {{ background: {ACCENT_HOVER}; }}
QPushButton#Primary:disabled {{ background: {ACCENT_DIM}; color: #9aa; }}

QLabel#ZoomLabel {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 6px;
    padding: 3px 8px; min-width: 46px; font-size: 9pt;
}}

QSlider::groove:horizontal {{ height: 4px; background: {BORDER}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    background: white; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px;
}}
QSlider::handle:horizontal:hover {{ background: {ACCENT_HOVER}; }}

QComboBox, QLineEdit, QSpinBox {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 4px 8px;
    selection-background-color: {ACCENT};
}}
QComboBox:hover, QLineEdit:hover, QSpinBox:hover {{ border-color: #3a3e48; }}
QComboBox:focus, QLineEdit:focus, QSpinBox:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox QAbstractItemView {{
    background: {SURFACE}; border: 1px solid {BORDER}; selection-background-color: {ACCENT_DIM};
    padding: 4px;
}}
QCheckBox {{ spacing: 8px; }}

QMenu {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 8px; padding: 6px;
}}
QMenu::item {{ padding: 7px 28px 7px 12px; border-radius: 5px; }}
QMenu::item:selected {{ background: {SURFACE2}; }}
QMenu::item:disabled {{ color: #5a5d66; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 5px 6px; }}
QMenu::icon {{ padding-left: 8px; }}

QToolTip {{
    background: {SURFACE2}; color: {TEXT}; border: 1px solid {BORDER}; border-radius: 6px; padding: 5px 8px;
}}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: #3a3d46; border-radius: 4px; min-width: 30px; margin: 2px; }}
QScrollBar::handle:horizontal:hover {{ background: #4a4e58; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QProgressBar {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 6px; height: 10px; text-align: center;
    font-size: 8pt;
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 5px; }}
QDialog {{ background: {PANEL}; }}
QFrame#Separator {{ background: {BORDER}; }}
#Toast {{
    background: rgba(30, 32, 38, 235); border: 1px solid {BORDER}; border-radius: 10px;
    padding: 9px 16px; font-size: 10pt;
}}
"""
