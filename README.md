# Annotate MEG/EEG Raw Data from a Table

[![Run on Brainlife.io](https://img.shields.io/badge/Brainlife-annotate--raw-blue.svg)](https://brainlife.io/apps)

## Description

Adds `mne.Annotations` to MNE raw MEG/EEG data from a table with one annotation per row. Optional filter columns (`subject`, `session`, `task`, `run`, `tag`, `dataset_id`) restrict a row to the input file(s) it describes, so a single table can be reused across a whole project, a pipeline rule, or a process with several inputs. A row with no filter applies to every input.

Annotations are built with `mne.Annotations` and set with `raw.set_annotations`. Onsets are in seconds from the first sample of each recording.

The app generates:
- The raw data with the annotations added (or replaced)
- A timeline figure per input showing the annotations already in the file and the ones added
- A `product.json` listing which table rows were applied to which input, with warnings for rows that matched nothing

## Inputs

- **`raw`** (`neuro/meeg/mne/raw`): continuous raw MEG/EEG data to annotate; a single file or a list of files, each handled independently (required)

## Outputs

- **`out_dir/raw.fif`** (`neuro/meeg/mne/raw`): the annotated raw data. With several inputs, one `out_dir/raw_<n>.fif` per input, in input order
- **`out_figs/annotations.png`**: annotation timeline (with several inputs, `out_figs/annotations_<n>.png`)
- **`product.json`**: summary of applied rows, warnings, and the timelines (the first five when there are more inputs)

## Configuration Parameters

| key | type | default | description |
|---|---|---|---|
| `annotations` | string (multiline) | none (required) | The annotation table (see below), or the path to a file containing it. |
| `mode` | string | `"append"` | `"append"` keeps the annotations already in the file; `"replace"` discards them. In both modes, an input that no table row applies to is saved unchanged. |
| `strict` | boolean | `false` | If `true`, a row that matches no input, or that starts after or runs past the end of a recording, is an error instead of a warning. |

### The annotation table

A delimited table with a **header row**. The delimiter is a tab if the header contains one, otherwise a comma. Blank lines and lines starting with `#` are ignored. Cells containing the delimiter must be quoted (`"..."`).

| column | required | meaning |
|---|---|---|
| `onset` | yes | start in seconds from the first sample, `>= 0` |
| `duration` | yes | length in seconds, `>= 0` |
| `description` | yes | label, e.g. `bad_muscle` (any text, hyphens included) |
| `channels` | no | channel names separated by `;` or `\|`; blank means all channels. Names must exist in the input |
| `subject`, `session`, `task`, `run` | no | filters on the input's BIDS-style identity |
| `tag` | no | filter on the input's tags: `;`-separated, every entry must match one of the input's tags or datatype tags (case-insensitive) |
| `dataset_id` | no | pins one specific brainlife dataset |

Example:

```
subject, run, tag,  onset, duration, description, channels
,        ,    ,     12.5,  2.0,      bad_muscle,
01,      3,   ,     40,    1.0,      bad_jump,    MEG0113;MEG0112
0*,      ,    rest, 0,     5,        bad_startup,
```

- Row 1 applies to every input.
- Row 2 applies to subject 01, run 3 only, on two channels.
- Row 3 applies to subjects starting with `0` whose tags include `rest`.

#### Filter cells

Each filter cell is a wildcard pattern or a regular expression:

- **Wildcards** (default): `*` any characters, `?` one character, `[12]` one of the listed characters. A plain value is an exact match. A blank cell or `*` means any.
- **Regular expression**: start the cell with `re:`, e.g. `re:0[1-3]` or `re:(rest|task)_.*`. It must match the whole value; add `(?i)` for case-insensitive. The prefix is required so that a bare `0*` is never silently read as a regex.

`subject`, `session`, `task` and `run` are compared leniently: a plain value is normalised on both sides (BIDS prefix `sub-`/`ses-`/`task-`/`run-` removed, leading zeros dropped from digit strings), so `1`, `01` and `sub-01` all match subject `01`. Wildcards and regexes are tried against the value as given, with and without the prefix, and without leading zeros.

A row applies to an input only if **every** filled filter matches. Matching is case-sensitive except for `tag`.

#### Where an input's identity comes from

1. The brainlife dataset metadata (`subject`, `session`, `run`, ...) and tags, passed to the app in `config.json`.
2. For fields the metadata does not provide, BIDS entities in the file name (`sub-01_ses-meg_task-rest_run-02_meg.fif`). This makes local runs and BIDS-imported data work.
3. If a field a row filters on is unknown for an input, the row is **not** applied to that input, and a warning says so. A filter is never silently satisfied by missing information.

## Usage

### Running on Brainlife.io

1. Select your raw MEG/EEG dataset(s) as the `raw` input.
2. Paste the annotation table into `annotations`, using filter columns to say which input each row is for.
3. Optionally set `mode` and `strict`.
4. Submit the process. To annotate many subjects with one table, run the app from a pipeline rule with the same `annotations` text.

### Local Testing

```bash
# Edit config.json to point "raw" at a raw .fif file, then:
python main.py

# End-to-end tests (synthetic recordings only):
python -m pytest tests
```

## Technical Details

- **Time reference**: `onset` is seconds from the first sample (`mne.Annotations(orig_time=None)`), independent of `meas_date`. Annotations already in the file are converted to the same frame before being combined, so `append` does not shift them.
- **Out-of-range rows**: a row starting after the end of a recording is dropped for it and one running past the end is cut, each with a warning (an error with `strict`).
- **Unmatched rows**: a row matching none of the inputs is reported, which catches typos in filters.
- **Output check**: each saved file is re-read with `mne.io.read_raw_fif` and checked to have lost no annotation before the app exits successfully.

## Limitations

- Raw data only. Epochs inherit annotations from the raw data they are cut from.
- The table is supplied as text in the `annotations` parameter (or as a file path when run locally); it is not a separate brainlife input.

## Pipeline Position

Typically runs on data from a conversion app (`*2mne`) or `mark-bad-raw`, before filtering or epoching.

## Authors

- Maximilien Chaumon (https://github.com/dnacombo)

## Citations

We kindly ask that you cite the following articles when publishing papers and code using this app:

Hayashi, S., Caron, B.A., Heinsfeld, A.S. et al. brainlife.io: a decentralized and open-source cloud platform to support neuroscience research. Nat Methods 21, 809–813 (2024). https://doi.org/10.1038/s41592-024-02237-2

Gramfort, A. et al. MEG and EEG data analysis with MNE-Python. Front. Neurosci. 7, 267 (2013). https://doi.org/10.3389/fnins.2013.00267

## Funding Acknowledgement

brainlife.io is publicly funded and for the sustainability of the project we kindly ask that you acknowledge the following funding sources:

[![NSF-BCS-1734853](https://img.shields.io/badge/NSF_BCS-1734853-blue.svg)](https://nsf.gov/awardsearch/showAward?AWD_ID=1734853)
[![NSF-BCS-1636893](https://img.shields.io/badge/NSF_BCS-1636893-blue.svg)](https://nsf.gov/awardsearch/showAward?AWD_ID=1636893)
[![NSF-ACI-1916518](https://img.shields.io/badge/NSF_ACI-1916518-blue.svg)](https://nsf.gov/awardsearch/showAward?AWD_ID=1916518)
[![NSF-IIS-1912270](https://img.shields.io/badge/NSF_IIS-1912270-blue.svg)](https://nsf.gov/awardsearch/showAward?AWD_ID=1912270)
[![NIH-NIBIB-R01EB029272](https://img.shields.io/badge/NIH_NIBIB-R01EB029272-green.svg)](https://grantome.com/grant/NIH/R01-EB029272-01)
[![NIH-NIBIB-R01EB030896](https://img.shields.io/badge/NIH_NIBIB-R01EB030896-green.svg)](https://grantome.com/grant/NIH/R01-EB030896-01)

## License

Copyright (c) 2026 MEEG Brainlife team. Licensed under AGPL-3.0, see [license.txt](license.txt).
