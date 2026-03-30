# Session Structure And Metadata Contract

This document defines the target on-disk structure and metadata contract for acquisition sessions that will later be consumed by `CaImAn Pipeline`.

## Folder Structure

Use this hierarchy:

```text
<ProjectName>/
  <Genotype>/
    <AgeGroup>/
      <Sex>/
        <AnimalID>/
          Slice_<N>/
            Session_<NNN>/
              raw_<YYYYMMDD_HHMMSS>/
              metadata/
              analysis/
                working/
                run_packets/
```

Example:

```text
KCL_Pilot/
  APOE3/
    Aged/
      Female/
        358/
          Slice_2/
            Session_001/
              raw_20260312_143320/
              metadata/
              analysis/
                working/
                run_packets/
```

## Naming Rules

### Controlled values

- `Genotype`
  - `APOE2`
  - `APOE3`
  - `APOE4`
  - `Other_<label>`
- `AgeGroup`
  - `Young`
  - `Aged`
- `Sex`
  - `Male`
  - `Female`

### Folder naming

- `AnimalID`
  - preserve the experiment-facing identifier, e.g. `358`
- `Slice_<N>`
  - integer slice number only
- `Session_<NNN>`
  - zero-padded session counter within the slice, e.g. `Session_001`
- `raw_<YYYYMMDD_HHMMSS>`
  - timestamped acquisition payload folder

### Normalization

For any free-text field used in paths:

- replace spaces with `_`
- remove characters invalid for Windows paths
- keep names short and stable

## Session Metadata

Create:

```text
Session_<NNN>/metadata/session_metadata.json
```

Recommended schema:

```json
{
  "schema_version": 1,
  "project_name": "KCL_Pilot",
  "genotype": "APOE3",
  "genotype_other": "",
  "age_group": "Aged",
  "sex": "Female",
  "animal_id": "358",
  "slice_number": 2,
  "session_id": "Session_001",
  "session_timestamp": "2026-03-12T14:33:20",
  "session_folder": "Session_001",
  "raw_folder": "raw_20260312_143320",
  "raw_path": "D:\\Scientifica\\...\\raw_20260312_143320",
  "metadata_path": "D:\\Scientifica\\...\\metadata",
  "analysis_path": "D:\\Scientifica\\...\\analysis",
  "input_mode": "folder_tiffs",
  "acquisition_mode": "continuous",
  "frame_rate_hz": 50.0,
  "exposure_ms": 20.0,
  "camera_device": "Hamamatsu",
  "mm_config_path": "C:\\Program Files\\Micro-Manager-2.0\\Scientifica.cfg",
  "preview_enabled": true,
  "operator": "",
  "experiment_type": "KCL",
  "notes": ""
}
```

### Required fields

- `schema_version`
- `project_name`
- `genotype`
- `age_group`
- `sex`
- `animal_id`
- `slice_number`
- `session_id`
- `session_timestamp`
- `raw_folder`
- `raw_path`
- `analysis_path`
- `acquisition_mode`
- `frame_rate_hz`

## Stimulation Events Metadata

Create:

```text
Session_<NNN>/metadata/stim_events.json
```

Recommended schema:

```json
{
  "schema_version": 1,
  "session_id": "Session_001",
  "events": [
    {
      "event_id": 1,
      "event_type": "kcl_addition",
      "timestamp_s": 120.5,
      "frame_index": 6025,
      "label": "KCl_low",
      "concentration_mM": 10.0,
      "duration_s": null,
      "ttl_channel": null,
      "ttl_pulse_width_ms": null,
      "notes": ""
    },
    {
      "event_id": 2,
      "event_type": "ttl_burst",
      "timestamp_s": 300.0,
      "frame_index": 15000,
      "label": "burst_1",
      "concentration_mM": null,
      "duration_s": 1.0,
      "ttl_channel": "DO0",
      "ttl_pulse_width_ms": 5.0,
      "notes": ""
    }
  ]
}
```

### Allowed `event_type` values

- `kcl_addition`
- `ttl_burst`
- `burst_start`
- `burst_end`
- `manual_annotation`

### Required event fields

- `event_id`
- `event_type`
- `timestamp_s`
- `label`

Recommended when available:

- `frame_index`
- `concentration_mM`
- `duration_s`
- `ttl_channel`
- `ttl_pulse_width_ms`

## Analysis App Expectations

`CaImAn Pipeline` should eventually be able to:

1. import `Session_<NNN>`
2. detect `raw_<timestamp>/`
3. read `metadata/session_metadata.json`
4. read `metadata/stim_events.json`
5. set analysis output root to:

```text
Session_<NNN>/analysis/
```

6. continue saving into:

```text
analysis/
  working/
  run_packets/
```

## Practical Integration Plan

1. Update the acquisition app to write the folder structure above.
2. Add `session_metadata.json` creation.
3. Add `stim_events.json` creation.
4. Add an `Import Acquisition Session` feature to `CaImAn Pipeline`.
5. Later, optionally add an `Acquisition` tab in `CaImAn Pipeline` that launches or wraps the acquisition app.

## Non-Goals For Now

- full GUI merge of PyQt acquisition into the Tk analysis app
- copying raw acquisition data into analysis packets
- batch analysis integration
