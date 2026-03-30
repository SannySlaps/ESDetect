# Mac Test Checklist

Use this when you are ready to validate the Mac build of ESDetect.

## 1. Clone and set up

```bash
git clone https://github.com/SannySlaps/ESDetect.git
cd ESDetect
conda env create -f environment.frontend.yml
conda activate esdetect-frontend
pip install pyinstaller
```

## 2. Build the app

```bash
cd suite2p_frontend
chmod +x build_ESDetect_frontend_mac.command
./build_ESDetect_frontend_mac.command
```

Expected output:

- `suite2p_frontend/dist/ESDetect/ESDetect.app`

## 3. First launch

Open:

- `suite2p_frontend/dist/ESDetect/ESDetect.app`

If macOS blocks it on first launch:

- right-click -> `Open`

## 4. Minimal validation

Test one real session from the portable drive:

1. open ESDetect
2. load a finished session
3. load ROI curation data
4. open an overlay preview
5. export a session summary CSV

## 5. Optional validation

If you want to go further:

1. rerender a standard overlay preview
2. test Portable Transfer paths
3. test manual ROI add only if the session copy includes:
   - `analysis/retained_temp/.../data.bin`

## 6. What to watch for

- app opens but immediately closes
- file-open actions fail
- overlay/video rendering fails
- missing session data on the portable drive
- manual ROI add fails because no retained `data.bin` exists

## 7. Current expectation

- Mac is intended primarily for:
  - curation
  - post-run review
  - exports
  - portable-drive workflow
- Windows remains the primary platform for:
  - acquisition
  - processing
  - batch execution
