# Suite2p Sandbox

This folder is a parallel, non-destructive sandbox for evaluating `suite2p`
alongside the current CaImAn-based workflow.

The goal is simple:

`Can Suite2p recover useful somatic ROIs on difficult sessions like Session_004 faster or more robustly than the current CaImAn path on this workstation?`

## Scope

Stage 1 is deliberately small:

- prepare a reproducible run folder for a session
- capture raw TIFF locations and current CaImAn reference artifacts
- store a basic Suite2p config handoff
- compare extraction quality and runtime before any deeper integration

This sandbox does **not** replace the current standalone app.

## Folder layout

- `configs/`
  - example config templates for Suite2p
- `runs/`
  - one folder per Suite2p experiment
- `scripts/`
  - helper utilities for preparing and launching experiments

## First-use workflow

```powershell
python ".\suite2p_sandbox\scripts\prepare_suite2p_session.py" `
  --session "D:\Scientifica\Hassan Pilots\KCL_Concentration_Pilot\APOE3\Aged\Male\358\Slice_1\Session_004"
```

This will create a run folder under `runs/` containing:

- `session_manifest.json`
- `source_paths.txt`
- `notes.md`
- `suite2p_db.json`
- `suite2p_ops_override.json`

By default, the prepared config uses:

- `fast_disk`: a local temp folder under `C:\Users\<you>\CaImAn\temp\suite2p\...`
- `save_path0`: a durable folder under the session HDD `analysis\suite2p_sandbox\...`

That keeps heavy temporary I/O off OneDrive while still saving final outputs next to the source session.

Then launch Suite2p with:

```powershell
conda activate suite2p
python ".\suite2p_sandbox\scripts\run_suite2p_session.py" `
  --run-dir "C:\Users\tur83376\OneDrive - Temple University\Coding Rig\Integrated Calcium Workflow\suite2p_sandbox\runs\Aged_Male_358_Slice_1_Session_004_suite2p_20260325_205845"
```

The launcher now also exports review artifacts automatically after a successful run:

- `suite2p_run.log`
- `suite2p_runtime.json`
- `suite2p_overlay_preview.mp4`
- `suite2p_motion_preview.mp4`
- `suite2p_contours.png`
- `suite2p_run_summary.json`

If you want to re-run just the export step on an existing finished run:

```powershell
conda activate suite2p
python ".\suite2p_sandbox\scripts\export_suite2p_artifacts.py" `
  --run-dir "C:\Users\tur83376\OneDrive - Temple University\Coding Rig\Integrated Calcium Workflow\suite2p_sandbox\runs\Aged_Male_358_Slice_1_Session_004_suite2p_20260325_205845"
```

If you want a standalone ROI-overlay preview only:

```powershell
conda activate suite2p
python ".\suite2p_sandbox\scripts\make_suite2p_overlay_preview.py" `
  --plane-dir "C:\Users\tur83376\OneDrive - Temple University\Coding Rig\Integrated Calcium Workflow\suite2p_sandbox\runs\Aged_Male_358_Slice_1_Session_004_suite2p_20260325_205845\outputs\suite2p\plane0" `
  --start-frame 0 `
  --num-frames 600
```

If you want to validate the config first without running:

```powershell
conda activate suite2p
python ".\suite2p_sandbox\scripts\run_suite2p_session.py" `
  --run-dir "C:\Users\tur83376\OneDrive - Temple University\Coding Rig\Integrated Calcium Workflow\suite2p_sandbox\runs\Aged_Male_358_Slice_1_Session_004_suite2p_20260325_205845" `
  --print-only
```

## First preprocessing experiment

If you want to test a soma-emphasized input set on one prepared run without changing the normal workflow:

```powershell
conda activate suite2p
python ".\suite2p_sandbox\scripts\preprocess_suite2p_input.py" `
  --run-dir "C:\Users\tur83376\OneDrive - Temple University\Coding Rig\Integrated Calcium Workflow\suite2p_sandbox\runs\Aged_Male_358_Slice_1_Session_004_suite2p_20260325_205845" `
  --sigma 12 `
  --gain 1.5 `
  --apply-to-db
```

This will:

- create a background-suppressed TIFF set under:
  - `run_dir\preprocessed_input\...`
- write:
  - `preprocessed_input_manifest.json`
- optionally repoint:
  - `suite2p_db.json -> data_path`

So the next `run_suite2p_session.py` launch uses the preprocessed TIFF directory for that run only.

Minimal comparison target:

1. one representative session
2. one preprocessed-input run
3. compare against your current best:
   - accepted overlay
   - obvious dendrites
   - obvious missed somas

## Soma-biased preprocessing experiment

If background subtraction alone still leaves dendrites winning over soma, try the
more blob-oriented recipe:

```powershell
conda activate suite2p
python ".\suite2p_sandbox\scripts\preprocess_suite2p_input.py" `
  --run-dir "C:\Users\tur83376\OneDrive - Temple University\Coding Rig\Integrated Calcium Workflow\suite2p_sandbox\runs\Aged_Male_358_Slice_1_Session_004_suite2p_20260325_205845" `
  --method soma_blob_enhance `
  --sigma 12 `
  --gain 1.5 `
  --blob-sigma 3 `
  --blob-weight 1.5 `
  --apply-to-db
```

This keeps the low-frequency background suppression, then adds a
difference-of-Gaussians boost that favors compact blob-like structure over long
thin processes.

## External Soma Proposal Prototype

If you want to step away from Suite2p ROI proposal entirely and start again
from the raw baseline, use the external prototype on a session directly.

For `Session_002`:

```powershell
conda activate suite2p
python ".\suite2p_sandbox\scripts\external_soma_proposal.py" `
  --session "D:\Scientifica\Hassan Pilots\KCL_Concentration_Pilot\APOE3\Aged\Male\359\Slice_1\Session_002" `
  --label "session002_trial01"
```

This writes a dedicated workspace under:

- `Session_002\analysis\external_soma_proposals\session002_trial01`

Key outputs:

- `images\mean_image.png`
- `images\max_image.png`
- `images\proposal_base.png`
- `images\proposal_residual.png`
- `images\proposal_soma_blob.png`
- `images\proposal_candidates_overlay.png`
- `proposal_candidates.json`
- `proposal_manifest.json`

The goal of this prototype is simple:

1. start again from raw TIFFs
2. build a soma-first proposal image stack
3. produce external candidate centers/overlays
4. compare those proposals against the best Suite2p overlays before building extraction

## External Soma Segmentation / Extraction Prototype

Once a proposal/segmentation trial looks acceptable, you can extract traces from
the external masks and package them into a minimal Suite2p-style `plane0`
folder.

Example baseline for `Session_002`:

- segmentation baseline:
  - `Session_002\analysis\external_soma_segmentation\session002_trial14_somablob_watershed_blobbiased_minfloor`

Extract traces:

```powershell
conda activate suite2p
python ".\suite2p_sandbox\scripts\external_soma_extract.py" `
  --segmentation-dir "D:\Scientifica\Hassan Pilots\KCL_Concentration_Pilot\APOE3\Aged\Male\359\Slice_1\Session_002\analysis\external_soma_segmentation\session002_trial14_somablob_watershed_blobbiased_minfloor" `
  --label "trial14_extract01"
```

Build ROI-by-ROI review pages:

```powershell
python ".\suite2p_sandbox\scripts\external_soma_trace_review.py" `
  --extraction-dir "D:\Scientifica\Hassan Pilots\KCL_Concentration_Pilot\APOE3\Aged\Male\359\Slice_1\Session_002\analysis\external_soma_extraction\trial14_extract01" `
  --label "review01" `
  --sort-by peak_dff
```

Package a minimal `plane0` dataset:

```powershell
python ".\suite2p_sandbox\scripts\external_soma_package.py" `
  --extraction-dir "D:\Scientifica\Hassan Pilots\KCL_Concentration_Pilot\APOE3\Aged\Male\359\Slice_1\Session_002\analysis\external_soma_extraction\trial14_extract01" `
  --label "trial14_plane0"
```

This writes:

- review pages under:
  - `Session_002\analysis\external_soma_extraction\trial14_extract01\review01`
- packaged curation-ready files under:
  - `Session_002\analysis\external_soma_packaged\trial14_plane0\plane0`

The packaged folder includes:

- `stat.npy`
- `iscell.npy`
- `ops.npy`
- `F.npy`
- `Fneu.npy`
- `spks.npy`
- `suite2p_mean_projection.png`
- `suite2p_max_projection.png`
- `suite2p_correlation_image.png`
- `suite2p_static_overlay.png`

## ESDetect Baseline

The current working detector baseline for difficult soma-first sessions is:

- `D:\Scientifica\suite2p_information\parameter_presets\ESDetect_recall_first_v3.json`

This is the current "good enough" ESDetect preset for daily use.

Key settings:

- `source_image = proposal_soma_blob_transient`
- `motion_correct = true`
- `transient_weight = 1.4`
- `thresh_q = 93.8`
- `min_area = 80`
- `bg_sigma = 14.0`
- `blob_sigma = 4.5`
- `blob_weight = 1.8`
- `peak_fraction = 0.18`
- `max_area = 1900`
- `dilate_iters = 2`

Supporting presets kept for comparison:

- `ESDetect_recall_first_preset.json`
  - earlier recall-first baseline
- `ESDetect_recall_first_v2.json`
  - more aggressive recall push that increased candidate count
- `ESDetect_recall_first_v3.json`
  - current preferred balance

Practical guidance:

1. use `ESDetect_recall_first_v3` as the default starting point
2. prefer curation/manual add for the rare missed soma
3. only revisit detector tuning if a future session shows a broader failure pattern

## ESDetect Video Reviews

Normal ESDetect runs now still generate the standard Suite2p-style previews:

- `suite2p_motion_preview.mp4`
- `suite2p_overlay_preview.mp4`
- `suite2p_three_panel_preview.mp4`
- `suite2p_reconstruction_preview.mp4`

Optional ESDetect-specific overlay videos are available from the `Video Previews`
tab in the ESDetect frontend and are no longer generated on every run by default.

Available optional presets:

- `quick_review`
  - shorter review movie for fast checking
- `presentation`
  - smoother playback for sharing results
- `full_session`
  - exact full-session overlay when needed

## What to compare against CaImAn

For each test, focus on:

1. runtime to first usable result
2. soma capture in the central field
3. number of obvious junk ROIs
4. ease of QC / manual review
5. whether we would need custom video outputs to make review comfortable

## Next likely steps

1. create a dedicated `suite2p` conda environment
2. install Suite2p
3. run one proof-of-concept on Session_004
4. compare outputs against the latest CaImAn run packet
