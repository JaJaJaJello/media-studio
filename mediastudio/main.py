import os
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPalette
from PySide6.QtWidgets import QApplication

from . import theme


def resource(name):
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return str(base / "assets" / name)


def _install_excepthook():
    """Windowed builds have no console: log unhandled errors and show them instead of dying silently."""
    import traceback
    log_dir = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "MediaStudio"

    def hook(etype, value, tb):
        text = "".join(traceback.format_exception(etype, value, tb))
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            with open(log_dir / "error.log", "a", encoding="utf-8") as f:
                f.write(text + "\n")
        except OSError:
            pass
        try:
            from PySide6.QtWidgets import QMessageBox
            if QApplication.instance():
                QMessageBox.critical(None, "Media Studio error", f"{value}\n\nDetails were saved to {log_dir / 'error.log'}")
        except Exception:
            pass
    sys.excepthook = hook


def main():
    _install_excepthook()
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MediaStudio.App")
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    app = QApplication(sys.argv)
    app.setApplicationName("Media Studio")
    app.setStyle("Fusion")
    pal = QPalette()
    for role, col in [(QPalette.Window, theme.PANEL), (QPalette.WindowText, theme.TEXT),
                      (QPalette.Base, theme.SURFACE), (QPalette.AlternateBase, theme.SURFACE2),
                      (QPalette.Text, theme.TEXT), (QPalette.Button, theme.SURFACE2),
                      (QPalette.ButtonText, theme.TEXT), (QPalette.Highlight, theme.ACCENT),
                      (QPalette.HighlightedText, "#ffffff"), (QPalette.ToolTipBase, theme.SURFACE2),
                      (QPalette.ToolTipText, theme.TEXT), (QPalette.PlaceholderText, theme.MUTED)]:
        pal.setColor(role, QColor(col))
    pal.setColor(QPalette.Disabled, QPalette.Text, QColor("#5a5d66"))
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, QColor("#5a5d66"))
    app.setPalette(pal)
    f = QFont("Segoe UI Variable Text")
    f.setPointSizeF(10)
    app.setFont(f)
    app.setStyleSheet(theme.STYLESHEET)
    ico = resource("icon.ico")
    if os.path.exists(ico):
        app.setWindowIcon(QIcon(ico))

    from .window import MainWindow
    w = MainWindow()
    w.show()
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if args:
        w.open_path(args[0], confirm=False)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
