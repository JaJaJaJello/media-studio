"""Per-user (HKCU) 'Open with' registration. Nothing here needs admin rights."""
import ctypes
import sys
import winreg

APP_KEY = "MediaStudio.exe"
PROGID = "MediaStudio.Media"
CAPS = r"Software\MediaStudio\Capabilities"
CLASSES = r"Software\Classes"

EXTS = [".png", ".jpg", ".jpeg", ".jfif", ".bmp", ".gif", ".webp", ".tif", ".tiff", ".ico", ".tga",
        ".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".wmv", ".flv", ".mpg", ".mpeg", ".ts", ".mts",
        ".m2ts", ".3gp", ".ogv", ".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus"]


def exe_path():
    return sys.executable if getattr(sys, "frozen", False) else None


def _set(path, name, value):
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_WRITE) as k:
        winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)


def _delete_tree(root, path):
    try:
        with winreg.OpenKey(root, path, 0, winreg.KEY_ALL_ACCESS) as k:
            while True:
                try:
                    sub = winreg.EnumKey(k, 0)
                except OSError:
                    break
                _delete_tree(root, path + "\\" + sub)
        winreg.DeleteKey(root, path)
    except FileNotFoundError:
        pass


def _notify():
    ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)


def register():
    exe = exe_path()
    if not exe:
        raise RuntimeError("Registration is only available from the packaged MediaStudio.exe")
    cmd = f'"{exe}" "%1"'
    icon = f'"{exe}",0'
    app = rf"{CLASSES}\Applications\{APP_KEY}"
    _set(app, "FriendlyAppName", "Media Studio")
    _set(app + r"\DefaultIcon", "", icon)
    _set(app + r"\shell\open\command", "", cmd)
    for ext in EXTS:
        _set(app + r"\SupportedTypes", ext, "")
    _set(rf"{CLASSES}\{PROGID}", "", "Media Studio file")
    _set(rf"{CLASSES}\{PROGID}\DefaultIcon", "", icon)
    _set(rf"{CLASSES}\{PROGID}\shell\open\command", "", cmd)
    for ext in EXTS:
        _set(rf"{CLASSES}\{ext}\OpenWithProgids", PROGID, "")
    # Shows up under Settings > Apps > Default apps > Media Studio
    _set(CAPS, "ApplicationName", "Media Studio")
    _set(CAPS, "ApplicationDescription", "View, annotate and trim images, GIFs and videos")
    for ext in EXTS:
        _set(CAPS + r"\FileAssociations", ext, PROGID)
    _set(r"Software\RegisteredApplications", "Media Studio", CAPS)
    _notify()


def unregister():
    HK = winreg.HKEY_CURRENT_USER
    _delete_tree(HK, rf"{CLASSES}\Applications\{APP_KEY}")
    _delete_tree(HK, rf"{CLASSES}\{PROGID}")
    for ext in EXTS:
        try:
            with winreg.OpenKey(HK, rf"{CLASSES}\{ext}\OpenWithProgids", 0, winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, PROGID)
        except OSError:
            pass
    _delete_tree(HK, r"Software\MediaStudio")
    try:
        with winreg.OpenKey(HK, r"Software\RegisteredApplications", 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, "Media Studio")
    except OSError:
        pass
    _notify()


def is_registered():
    exe = exe_path()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"{CLASSES}\{PROGID}\shell\open\command") as k:
            val, _ = winreg.QueryValueEx(k, "")
            return bool(exe) and exe.lower() in val.lower()
    except OSError:
        return False
