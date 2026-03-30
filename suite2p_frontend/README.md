# suite2p_frontend

This folder contains the desktop frontend code used by ESDetect.

## What is here

- `external_soma_frontend_app/`
  - the ESDetect-oriented frontend app
- `suite2p_frontend_app/`
  - the base Suite2p-oriented frontend code
- `launch_ESDetect_frontend.cmd`
  - Windows launcher for source-based use
- `launch_ESDetect_frontend_mac.command`
  - macOS launcher for source-based use
- `ESDetectFrontend.spec`
  - PyInstaller spec used to build packaged apps
- `build_ESDetect_frontend_exe.ps1`
  - Windows packaging helper
- `build_ESDetect_frontend_mac.command`
  - macOS packaging helper

## Source launch

Windows:

```powershell
.\launch_ESDetect_frontend.cmd
```

macOS:

```bash
chmod +x ./launch_ESDetect_frontend_mac.command
./launch_ESDetect_frontend_mac.command
```

You can also run from Python directly if you already have the right environment:

```powershell
python .\external_soma_frontend_app\main.py
```

## Packaging

Windows:

```powershell
conda activate esdetect-frontend
pip install pyinstaller
.\build_ESDetect_frontend_exe.ps1
```

macOS:

```bash
conda activate esdetect-frontend
pip install pyinstaller
chmod +x ./build_ESDetect_frontend_mac.command
./build_ESDetect_frontend_mac.command
```

## Notes

- The frontend is intended to be shareable across machines.
- Avoid treating workstation-specific paths as fixed requirements.
- Default data roots such as `D:\Scientifica` or local temp locations are only defaults and can vary by machine.
- For broader workflow guidance, use the top-level repo `README.md`.
