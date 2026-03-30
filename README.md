# ESDetect

Custom Suite2p / ESDetect desktop app for:
- Windows-side session processing
- ROI curation
- post-run review
- downstream exports
- portable-drive handoff for Mac curation/downstream work

## Included
- `Acquisition and Stim/`
  - trimmed acquisition app bundle
  - Windows and Mac launchers
  - acquisition docs
- `suite2p_frontend/`
  - base Suite2p frontend code
  - ESDetect app code
  - ESDetect Windows and Mac launchers
  - Mac curation setup guide
- `suite2p_sandbox/`
  - processing / export scripts
  - configs
  - ESDetect presets
  - vendored `suite2p` fallback code

## Not included
- session data
- run outputs
- temp files
- analysis folders
- local virtual environments

## Recommended use

### Windows
- run ESDetect / Suite2p processing
- generate `plane0` outputs
- generate previews and summaries
- retain `data.bin` when manual ROI add may be needed

### Mac
- use for curation and downstream work
- open sessions from a portable drive copy
- review overlays / figures
- export summaries and reports

## Platform caveats

### Windows build
- This is the primary full-feature build target.
- Intended to include:
  - acquisition launching
  - batch run management
  - processing execution
  - Windows-specific setup utilities
  - curation and downstream tools
- Best choice for:
  - running ESDetect end-to-end
  - managing local processing environments
  - acquisition/workstation use
- Caveat:
  - packaged builds still rely on a usable external `suite2p` Python environment for subprocess-based processing tasks.

### Mac build
- This is the secondary build target.
- Intended primarily for:
  - ROI curation
  - post-run review
  - exports
  - portable-drive workflow
  - likely video/overlay rendering if OpenCV codec support is present
- Do not assume feature parity with the Windows build for:
  - acquisition launching
  - batch processing
  - Windows-only setup utilities
  - local processing environment management
- Best choice for:
  - reviewing and curating already processed sessions copied from the Windows workstation
  - downstream analysis on a portable/external-drive workflow
- Caveats:
  - `.app` must be built on macOS.
  - OpenCV MP4 writing should be validated on the target Mac.
  - manual ROI add still requires the retained `data.bin` to be present in the session copy.

## Environment files

Frontend / curation / downstream:
- `environment.frontend.yml`
- `requirements.frontend.txt`
- use this env to run ESDetect and build packaged desktop apps

Acquisition:
- `environment.acquisition.yml`
- `requirements.acquisition.txt`

Suggested conda setup:

```bash
conda env create -f environment.frontend.yml
conda env create -f environment.acquisition.yml
```

For desktop app packaging:

```bash
conda activate esdetect-frontend
pip install pyinstaller
```

## Portable-drive workflow
The app includes a `Portable Transfer` tab that can:
- export unfinished sessions from a desktop project root to a portable project root
- import updated unfinished sessions back to the desktop root
- preserve the full project-relative `Session_*` folder structure

## Launchers

### Windows
- `suite2p_frontend/launch_ESDetect_frontend.cmd`
- `Acquisition and Stim/launch_acquisition_windows.cmd`
- `suite2p_frontend/build_ESDetect_frontend_exe.ps1`

### Mac
- `suite2p_frontend/launch_ESDetect_frontend_mac.command`
- `Acquisition and Stim/launch_acquisition_mac.command`
- `suite2p_frontend/build_ESDetect_frontend_mac.command`

See:
- `suite2p_frontend/MAC_CURATION_SETUP.md`

## Packaging ESDetect

### Windows `.exe`

```powershell
cd suite2p_frontend
conda activate esdetect-frontend
.\build_ESDetect_frontend_exe.ps1
```

Expected output:
- `suite2p_frontend/dist/ESDetect/ESDetect.exe`

### macOS `.app`

```bash
cd suite2p_frontend
conda activate esdetect-frontend
chmod +x build_ESDetect_frontend_mac.command
./build_ESDetect_frontend_mac.command
```

Expected output:
- `suite2p_frontend/dist/ESDetect/ESDetect.app`

### Important note
- Build the `.exe` on Windows.
- Build the `.app` on macOS.
- The packaged app still expects a usable external `suite2p` Python environment for subprocess-based processing tasks.
- For curation/downstream-only use, this is usually fine.

## Minimal setup notes
- Python environment should include:
  - `numpy`
  - `scipy`
  - `matplotlib`
  - `pillow`
  - `openpyxl`
  - `opencv-python`
- `tkinter` must be available
- video rendering depends on OpenCV codec support on the host machine

## Suggested GitHub publish flow
1. Create a new empty GitHub repo.
2. Copy this folder's contents into that repo working tree.
3. Initialize git if needed.
4. Commit and push.

## Notes
- This repo is prepared to be cross-platform for curation/downstream use first.
- Full Windows processing support remains the primary execution path.
