@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "CONDA_ACTIVATE=%USERPROFILE%\AppData\Local\miniconda3\Scripts\activate.bat"
set "APP_MAIN=%SCRIPT_DIR%external_soma_frontend_app\main.py"
set "APP_PY="

if defined SUITE2P_ENV_PYTHON if exist "%SUITE2P_ENV_PYTHON%" (
    set "APP_PY=%SUITE2P_ENV_PYTHON%"
)

if not defined APP_PY if exist "%USERPROFILE%\AppData\Local\miniconda3\envs\suite2p\python.exe" (
    set "APP_PY=%USERPROFILE%\AppData\Local\miniconda3\envs\suite2p\python.exe"
)

if not exist "%APP_MAIN%" (
    echo Could not find External Soma frontend main.py:
    echo %APP_MAIN%
    pause
    exit /b 1
)

if defined APP_PY (
    "%APP_PY%" "%APP_MAIN%"
) else if exist "%CONDA_ACTIVATE%" (
    call "%CONDA_ACTIVATE%" suite2p
    python "%APP_MAIN%"
) else (
    python "%APP_MAIN%"
)

if errorlevel 1 (
    echo.
    echo External Soma frontend exited with an error.
    pause
)

endlocal
