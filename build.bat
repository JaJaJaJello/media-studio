@echo off
rem Rebuilds dist\MediaStudio\MediaStudio.exe. Run from this folder.
cd /d "%~dp0"
if not exist .venv (
    python -m venv .venv
    .venv\Scripts\python.exe -m pip install PySide6-Essentials av numpy sounddevice pillow imageio-ffmpeg pyinstaller
)
if not exist assets\icon.ico .venv\Scripts\python.exe make_icon.py
if not exist build_bin mkdir build_bin
for /f "delims=" %%i in ('.venv\Scripts\python.exe -c "import imageio_ffmpeg;print(imageio_ffmpeg.get_ffmpeg_exe())"') do copy /y "%%i" build_bin\ffmpeg.exe >nul
.venv\Scripts\pyinstaller.exe --noconfirm --clean --windowed --name MediaStudio --icon assets\icon.ico ^
    --add-data "assets;assets" --add-binary "build_bin\ffmpeg.exe;." ^
    --exclude-module tkinter --exclude-module imageio_ffmpeg run.py
rem software OpenGL fallback and Qt translations are unused
del /q dist\MediaStudio\_internal\PySide6\opengl32sw.dll 2>nul
rmdir /s /q dist\MediaStudio\_internal\PySide6\translations 2>nul
echo.
echo Built: %~dp0dist\MediaStudio\MediaStudio.exe
