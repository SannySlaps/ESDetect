@echo off
setlocal

set "CONDA_ACTIVATE=%USERPROFILE%\AppData\Local\miniconda3\Scripts\activate.bat"
set "APP_MAIN=C:\Users\tur83376\OneDrive - Temple University\Coding Rig\Integrated Calcium Workflow\suite2p_frontend\external_soma_frontend_app\main.py"

if not exist "%CONDA_ACTIVATE%" (
    echo Could not find conda activate script:
    echo %CONDA_ACTIVATE%
    pause
    exit /b 1
)

if not exist "%APP_MAIN%" (
    echo Could not find External Soma frontend main.py:
    echo %APP_MAIN%
    pause
    exit /b 1
)

call "%CONDA_ACTIVATE%" suite2p
python "%APP_MAIN%"

if errorlevel 1 (
    echo.
    echo External Soma frontend exited with an error.
    pause
)

endlocal
