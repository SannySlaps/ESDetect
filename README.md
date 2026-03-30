# Suite2p ESDetect Frontend

Custom Suite2p / ESDetect desktop frontend for:
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
  - base Suite2p frontend
  - ESDetect frontend
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

## Portable-drive workflow
The app includes a `Portable Transfer` tab that can:
- export unfinished sessions from a desktop project root to a portable project root
- import updated unfinished sessions back to the desktop root
- preserve the full project-relative `Session_*` folder structure

## Launchers

### Windows
- `suite2p_frontend/launch_ESDetect_frontend.cmd`
- `Acquisition and Stim/launch_acquisition_windows.cmd`

### Mac
- `suite2p_frontend/launch_ESDetect_frontend_mac.command`
- `Acquisition and Stim/launch_acquisition_mac.command`

See:
- `suite2p_frontend/MAC_CURATION_SETUP.md`

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
