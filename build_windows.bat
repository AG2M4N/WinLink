@echo off
REM Builds dist\WinLink\WinLink.exe. Needs Python 3.11+ from python.org (tick "Add python.exe to PATH").
REM A folder build (not one big .exe): it starts and quits at once, instead of unpacking itself each time.
cd /d "%~dp0"
echo Installing what WinLink needs...
py -m pip install --upgrade -r requirements-windows.txt pyinstaller || goto :err
if not exist bin\linkts-windows-amd64.exe (
  echo Building the Tailscale helper...
  where go >nul 2>nul || (echo The Tailscale helper is missing: install Go from go.dev, or use the zip from the Releases page, which includes it. & goto :err)
  if not exist bin mkdir bin
  pushd helper
  set CGO_ENABLED=0
  set GOOS=windows
  set GOARCH=amd64
  go build -trimpath -ldflags "-s -w -H windowsgui" -o ..\bin\linkts-windows-amd64.exe . || (popd & goto :err)
  set GOARCH=arm64
  go build -trimpath -ldflags "-s -w -H windowsgui" -o ..\bin\linkts-windows-arm64.exe . || (popd & goto :err)
  set GOOS=
  set GOARCH=
  popd
)
set OPUS=
if exist bin\opus.dll set OPUS=--add-binary bin\opus.dll;. --add-data bin\opus-COPYING.txt;.
if not defined OPUS echo No bin\opus.dll (packaging\build_opus.sh builds it): sound over Tailscale will go as PCM.
echo Building WinLink.exe...
if exist dist\WinLink.exe del /q dist\WinLink.exe
py -m PyInstaller --noconfirm --clean --onedir --windowed --name WinLink --icon assets\WinLink.ico --paths src --add-data "src\webui;webui" --collect-all webview --collect-all pythonnet --collect-all clr_loader --collect-submodules zeroconf --collect-submodules pystray --add-data "assets\WinLink.png;." --add-binary "bin\linkts-windows-amd64.exe;." --add-binary "bin\linkts-windows-arm64.exe;." %OPUS% src\link.py || goto :err
echo.
echo Done. Your app is dist\WinLink\WinLink.exe. Keep the whole WinLink folder together;
echo you can move the folder anywhere and pin WinLink.exe to Start or the taskbar.
start "" explorer dist\WinLink
pause
exit /b 0
:err
echo.
echo Build failed. Scroll up for the error.
pause
exit /b 1
