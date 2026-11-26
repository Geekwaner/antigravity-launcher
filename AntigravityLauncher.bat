@echo off
setlocal
chcp 65001 >nul
set "HERE=%~dp0"

rem If CLI arguments are provided, run in console mode; otherwise launch GUI without console window.
if "%~1"=="" goto :gui_mode

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 "%HERE%aglaunch.py" %*
    exit /b %errorlevel%
)

where python >nul 2>nul
if %errorlevel%==0 (
    python "%HERE%aglaunch.py" %*
    exit /b %errorlevel%
)

goto :no_python

:gui_mode
where pyw >nul 2>nul
if %errorlevel%==0 (
    pyw -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
    if %errorlevel%==0 (
        start "" pyw -3 "%HERE%aglaunch.py"
        goto :eof
    )
)

where pythonw >nul 2>nul
if %errorlevel%==0 (
    pythonw -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
    if %errorlevel%==0 (
        start "" pythonw "%HERE%aglaunch.py"
        goto :eof
    )
)

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 "%HERE%aglaunch.py"
    goto :eof
)

where python >nul 2>nul
if %errorlevel%==0 (
    python "%HERE%aglaunch.py"
    goto :eof
)

:no_python
echo [ERROR] Python 3.10+ not found in PATH.
echo Please install Python 3.10+ from https://www.python.org/downloads/windows/
pause
exit /b 1
