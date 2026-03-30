# Mac Curation / Downstream Setup

## Goal
- Run processing on Windows.
- Copy project/session folders to a portable drive.
- Use the Mac for ROI curation, review, overlay rendering, summaries, and downstream exports.

## What should already exist
- A portable drive copy of your project/session folders.
- For manual ROI add, the session should also contain:
  - `analysis/retained_temp/.../data.bin`

## Recommended Python environment
1. Install Miniconda or Anaconda on the Mac.
2. Create or reuse a `suite2p` environment.
3. Install these packages in that environment:
   - `python`
   - `numpy`
   - `scipy`
   - `matplotlib`
   - `pillow`
   - `openpyxl`
   - `opencv-python`

Example:

```bash
conda create -n suite2p python=3.11 -y
conda activate suite2p
pip install numpy scipy matplotlib pillow openpyxl opencv-python
```

## Tkinter check
- The frontend uses `tkinter`.
- On many Mac Python installs it is already available.
- Verify with:

```bash
python -m tkinter
```

- If a small Tk window opens, you are good.

## Optional Suite2p package check
- For curation/downstream work, the frontend can often run without full processing.
- Still, if you want the same environment shape as Windows, you can also install `suite2p`.

## Video rendering note
- The preview and overlay render scripts use OpenCV `VideoWriter` with `mp4v`.
- This should work on Mac if your OpenCV build has MP4 codec support.
- Quick sanity test after setup:
  - open one processed session
  - re-render an overlay preview
  - confirm an `.mp4` is produced and opens

## Launchers
- ESDetect frontend:
  - `launch_ESDetect_frontend_mac.command`
- Base Suite2p frontend:
  - `launch_suite2p_frontend_mac.command`

## First-run checklist
1. Mount the portable drive.
2. Activate the `suite2p` environment.
3. Launch the frontend.
4. Set project/session roots to the portable-drive paths.
5. Load one finished session.
6. Load ROI curation data.
7. Open a figure or overlay preview.
8. Build a session summary CSV.

## Manual ROI add requirement
- Manual ROI add needs the retained registered binary:
  - `analysis/retained_temp/.../data.bin`
- If that file is missing, manual add will fail even if the rest of curation works.

## Good first test session
- Pick one session that:
  - already has complete `plane0` outputs
  - already has `analysis/retained_temp`
  - already has preview artifacts

That gives you the cleanest Mac-side validation pass.
