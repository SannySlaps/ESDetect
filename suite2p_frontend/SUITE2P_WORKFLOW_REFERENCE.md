# Suite2p Workflow Reference

## Big Picture
The Suite2p app now supports three connected modes of work:

1. running Suite2p on a single session or a batch of sessions
2. reviewing and exporting post-run artifacts
3. revising ROI decisions later and folding those revisions back into the pipeline

The workflow is no longer just:

- run once
- inspect once
- done

It is now closer to:

- run
- inspect
- revise
- snapshot
- promote
- regenerate outputs
- continue downstream analysis

## Core Structure
The main operational tabs are:

1. `Run Manager`
2. `Analysis Parameters`
3. `Video Previews`
4. `Post-Run`

Supporting tabs:

- `Notifications`
- `Definitions`
- `Help`

## 1. Run Manager
This is the execution tab. It handles:

- one-off session runs
- queued batch runs
- preflight checks
- stop-after-current-session behavior
- session queue management

For a single run:

- choose a `Session Folder`
- set overwrite/archive behavior if needed
- click `Run From Session`

For a batch:

- add sessions manually or from a parent folder
- run preflight
- launch sequential processing
- track results in the batch panel

The purpose of `Run Manager` is orchestration. It is not the main place for review or curation.

### Useful run behavior already in place

- batch preflight for disk/session readiness
- optional skip if outputs already exist
- optional archive-before-overwrite
- batch result tracking
- better scrolling and path visibility in queue/results

## 2. Analysis Parameters
This tab controls the Suite2p parameters that affect:

- registration
- ROI detection
- soma sizing behavior
- thresholding
- overlap rules
- sparse mode / denoising style

Examples:

- `diameter`
- `threshold_scaling`
- `nonrigid`
- `maxregshift`
- `max_overlap`
- `soma_crop`

This is where you make analysis-level changes. If you change these values, you should usually rerun Suite2p.

### Two classes of change

There are two different kinds of change in the workflow:

1. analysis-parameter changes
- require rerunning Suite2p
- because detection/registration changes

2. curation changes
- happen after the run
- usually accept/reject or ROI-state adjustments
- can be propagated through exports without rerunning the full analysis

That distinction is central to the revised workflow.

## 3. Video Previews
This tab controls the review media outputs:

- `Motion Preview`
- `ROI Overlay Preview`
- `3-Panel Preview`
- `Reconstruction Preview`

Each preview has independent controls for:

- start frame
- number of frames
- fps
- gain
- `q_min`
- `q_max`

This tab is best understood as presentation/export tuning for inspection. It does not change the scientific result.

### Current preview source behavior

If the registered `.bin` still exists:

- previews render from the original movie source

If the `.bin` is gone but `motion_preview.mp4` exists:

- Overlay and 3-Panel can fall back to the saved preview video

`Reconstruction Preview`:

- is rebuildable from saved Suite2p outputs
- does not depend on the `.bin`

So preview rendering now has graceful fallback behavior.

## 4. Post-Run
This is the main post-analysis hub. It is organized around:

- `Load / Recovery` for reopening past sessions
- `ROI Snapshots` for revision checkpoints
- `Review / QC` for figures, reports, and edit actions
- `Export Tools` for rebuilding artifacts and summaries
- `Project Tools` for across-session exports and summaries

You can think of `Post-Run` as everything that happens after the initial run.

## Run Outputs
Durable Suite2p outputs live under:

- `Session_###\analysis\outputs\suite2p\plane0`

Prepared run metadata lives under:

- `Session_###\suite2p_runs\...`

So:

- `suite2p_runs` = run history / configuration / metadata
- `analysis\outputs\suite2p\plane0` = current active durable output set

That split matters because revisions mostly act on the durable output side.

## Post-Run Layout
`Load / Recovery`

- enter a `Session Path`
- click `Load Session`
- this is the top section because it is the main way to reopen past work

`ROI Snapshots`

- `Refresh Snapshots`
- `Load Snapshot For Review`
- `Use Active plane0`
- `Promote Snapshot To Active`
- `Open Snapshot Folder`

`Review / QC`

- `Figures`
- `Reports`
- `Actions`

`Export Tools`

- `Build`
- `Trace CSVs`
- `Package / Folders`

## Review Artifacts Available
Static artifacts:

- contours
- accepted contours
- rejected contours
- mean projection
- max projection
- correlation image
- static overlay image
- trace preview
- ROI size summary
- QC summary
- run summary JSON

Trace exports:

- accepted `F` traces CSV
- accepted `dF/F` traces CSV
- rejected `F` traces CSV
- rejected `dF/F` traces CSV

Video artifacts:

- motion preview
- ROI overlay preview
- 3-panel preview
- reconstruction preview

## What Reconstruction Preview Actually Is
`Reconstruction Preview` is:

- a synthetic activity visualization derived from Suite2p ROI footprints and traces

It is not:

- raw video
- motion-corrected video
- a true CNMF generative reconstruction

In practice it shows:

- where the accepted ROIs are
- how active they are over time
- overlaid onto a faint mean-image background

Why it is useful:

- it persists without the temp `.bin`
- it changes meaningfully when ROI selections change
- it gives a cleaner activity-centric review than the raw movie alone

## Manual Revision / Curation Workflow
You can now revise ROI decisions after the initial run.

Current curation model:

- primarily accept/reject editing through the Suite2p GUI
- then rebuilding downstream artifacts from the edited outputs

Workflow:

1. run Suite2p
2. inspect outputs in `Post-Run`
3. launch Suite2p GUI
4. edit ROI accept/reject state
5. save edits in Suite2p
6. return to the frontend
7. click `Finalize ROI Edits`
8. optionally click `Save Curation Snapshot`

### What Finalize ROI Edits does

- re-reads the edited Suite2p outputs
- rebuilds review artifacts
- rebuilds session summary CSV
- attempts event summary CSV if metadata allows

It is the bridge between manual curation and the rest of the pipeline.

## Curation Snapshots
Snapshots live under:

- `Session_###\analysis\outputs\suite2p_curated_snapshots\curated_snapshot_YYYYMMDD_HHMMSS`

A snapshot is:

- a timestamped copy of the current `plane0` output state

Purpose:

- preserve a curated ROI set
- allow later comparison
- restore or review an older edited state
- keep version history without immediately overwriting the active output set

## Snapshot Operations
The app supports:

- `Refresh Snapshots`
- `Load Snapshot For Review`
- `Use Active plane0`
- `Promote Snapshot To Active`
- `Open Snapshot Folder`

### Load Snapshot For Review
This means:

- use this saved ROI state as the current review/analysis source

It affects:

- artifact opening
- ROI-state-based analyses
- summary/trace-derived inspection

It does not overwrite the active outputs.

### Use Active plane0
This means:

- go back to the current working output set

No files are modified.

### Promote Snapshot To Active
This means:

- make this snapshot the working `plane0` output state

Before promotion:

- the current active `plane0` is backed up automatically

Then:

- the selected snapshot is copied into active `plane0`

This cleanly separates review from overwrite.

## How Revisions Fit Into the Pipeline

### Pipeline A: initial run

1. choose session
2. set analysis parameters
3. `Run From Session`
4. review artifacts
5. export if acceptable

### Pipeline B: revise ROI decisions

1. load finished run in `Post-Run`
2. launch Suite2p GUI
3. edit ROIs
4. save in Suite2p
5. `Finalize ROI Edits`
6. `Save Curation Snapshot`

At this point you have:

- updated active outputs
- a versioned snapshot

### Pipeline C: compare / revisit revisions later

1. load the run in `Post-Run`
2. `Refresh Snapshots`
3. `Load Snapshot For Review`
4. inspect the older ROI state
5. if desired, `Promote Snapshot To Active`
6. regenerate/export from the promoted state

## What Gets Rebuilt After Revision
Always rebuildable from saved Suite2p outputs:

- contours
- accepted/rejected contours
- mean projection
- max projection
- correlation image
- static overlay image
- trace preview
- trace CSVs
- QC summary
- reconstruction preview
- session/event summary CSVs

Rebuildable as videos if suitable source exists:

- overlay preview
- 3-panel preview

Best case:

- temp `.bin` still exists

Fallback case:

- `motion_preview.mp4` exists, so Overlay and 3-Panel can be rebuilt from the saved preview video

Not truly recoverable if both sources are gone:

- motion preview itself
- true movie-based overlay rendering without any saved frame source

## Static Overlay
`Static Overlay` is:

- `meanImg` + current ROI overlay

It gives you:

- a durable visual confirmation of the current ROI set
- something that can always be rebuilt after promotion from saved outputs alone

This is especially useful when:

- you revised ROIs
- temp storage was cleaned
- you still want a clear visual summary of the revised segmentation

## Revised Video Behavior

### Overlay Preview

- ideal source: registered movie
- fallback source: saved motion preview
- current ROI set is redrawn onto it

### 3-Panel Preview

- ideal source: raw + motion-corrected movie
- fallback source: saved motion preview
- the right panel is rebuilt from the current ROI mask

### Reconstruction Preview

- built from ROI traces and footprints
- current ROI outlines are also drawn on it
- no `.bin` required

## Recommended Real-World Workflow

### Phase 1: parameter tuning

1. pick a representative session
2. adjust biological parameters in `Analysis Parameters`
3. run that one session
4. inspect:
   - static overlay
   - contours
   - accepted/rejected contours
   - reconstruction preview
   - 3-panel preview
5. if detection is wrong, change parameters and rerun

### Phase 2: production running

1. lock a good parameter set
2. batch preflight
3. run batch sequentially
4. inspect outputs session by session as needed

### Phase 3: revision / curation

For sessions that need cleanup:

1. load the finished run in `Post-Run`
2. launch Suite2p GUI
3. revise accept/reject
4. `Finalize ROI Edits`
5. `Save Curation Snapshot`

### Phase 4: version control of curation

1. if you make another revision later, save another snapshot
2. compare snapshots by loading them for review
3. promote the preferred snapshot to active
4. export updated artifacts/package

### Phase 5: downstream packaging

Once satisfied:

1. export artifacts
2. build session/event summary CSVs
3. export downstream package
4. optionally generate project summary workbook/plots/report

## What Is Necessary vs Optional
Truly important operations:

- `Run From Session`
- `Load Session`
- `Launch Suite2p GUI`
- `Finalize ROI Edits`
- `Save Curation Snapshot`
- `Load Snapshot For Review`
- `Promote Snapshot To Active`
- `Export Artifacts`

Helpful but secondary:

- manual preview rerenders
- downstream package export
- project-level summary tools
- notifications
- snapshot folder opening

## Best Mental Model
The cleanest way to think about the system is:

- `Analysis Parameters` changes the algorithm
- `Run Manager` executes the algorithm
- `Post-Run` inspects the result
- `Suite2p GUI` revises the result
- `Snapshots` version the revisions
- `Promote` chooses which revision becomes current
- `Export` regenerates deliverables from that chosen state

## Where This Is Strong Now
The app is now strong at:

- rerunnable session/batch execution
- richer review artifact generation
- post-run ROI revision
- versioned curation snapshots
- promotion of curated states into active outputs
- partial preview regeneration even after temp cleanup
- downstream packaging and summary generation

## Remaining Conceptual Limitation
The main limitation to keep in mind:

- Suite2p ROI revision here is strongest for accept/reject-style editing
- it is not yet a custom manual "draw new soma from scratch" curation environment

So the revision pipeline is already very useful, but it is still fundamentally:

- revise the Suite2p result

rather than:

- fully hand-segment from zero

## Recommended Standard Operating Pattern

1. tune parameters on a representative session
2. batch run with those settings
3. use Post-Run as the curation hub
4. save snapshots after meaningful ROI revisions
5. promote only the curated state you actually want as final
6. export downstream only after that promotion/finalization step

That keeps:

- parameter tuning
- curation
- version history
- final export

cleanly separated.
