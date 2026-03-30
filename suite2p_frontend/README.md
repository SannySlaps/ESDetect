# Suite2p Frontend

This is a separate desktop app that keeps the general feel of the CaImAn
frontend while using the `suite2p_sandbox` backend.

The current version focuses on:

- running one session or a queued set of sessions from session folders
- launching Suite2p with the dedicated `suite2p` conda environment
- exporting review artifacts automatically
- opening the Suite2p GUI or generated output folders
- reloading an older run directory or a saved output folder later for review
- exposing key Suite2p runtime, motion, detection, and biological parameters
- parameter definitions and workflow help
- optional email notifications for background actions

It does not modify the production CaImAn app.

## Launch

From PowerShell:

```powershell
conda activate suite2p
python ".\Integrated Calcium Workflow\suite2p_frontend\suite2p_frontend_app\main.py"
```

## Current workflow

The current app layout is:

- `Run Manager`
- `Analysis Parameters`
- `Video Previews`
- `Post-Run`
- `Notifications`
- `Definitions`
- `Help`

Recommended flow:

1. In `Run Manager`, either:
   - select one `Session_###` folder and use `Run From Session`
   - or queue one or more sessions for sequential processing
2. In `Analysis Parameters`, load or save a parameter preset if needed
3. In `Video Previews`, re-render or open the generated videos with per-video settings
4. In `Post-Run`, reopen older results, review QC artifacts, export downstream files, and build project-level summaries
5. In `Notifications`, save SMTP settings and send a test email if desired
6. In `Definitions` and `Help`, use the built-in reference material while tuning or troubleshooting

For later review of an older result:

1. Use `Load Run Dir` if you still have the prepared run folder
2. Or use `Load Output Folder` and point it at:
   - `plane0`
   - `suite2p`
   - or the parent `outputs` folder

Prepared run metadata lives under each session:

- `Session_###\suite2p_runs`

Durable session outputs are written back under the session analysis folder on the HDD, typically:

- `Session_###\analysis\outputs\suite2p\plane0`

Shared Suite2p information lives under:

- `D:\Scientifica\suite2p_information`

SSD temp files are written under:

- `C:\Users\tur83376\suite2p\temp`
