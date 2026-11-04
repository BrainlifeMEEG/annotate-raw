"""
Annotate MEG/EEG raw data from a table of annotations scoped to specific inputs.

Each table row is one annotation (onset, duration, description, optional channels).
Optional filter columns (subject, session, task, run, tag, dataset_id) restrict a row
to the input file(s) it describes, so a single table can be reused across a whole
project, a pipeline rule or a multi-input process. A row with no filters applies to
every input.

Inputs:
    - raw: Path to an MNE raw .fif file, or a list of paths (each is handled
      independently)
    - annotations: Delimited table (comma or tab) with a header row, or a path
      to such a file. Columns: onset, duration, description [, channels] and
      any of the filter columns subject, session, task, run, tag, dataset_id.
      Filter cells are shell wildcards (*, ?, [12]) or, with a "re:" prefix, a
      regular expression matching the whole value.
    - mode: "append" (default) keeps annotations already in the file,
      "replace" discards them (only for inputs at least one row applies to)
    - strict: if true, a row matching no input or lying outside the recording is
      an error instead of a warning

Outputs:
    - out_dir/raw.fif (or out_dir/raw_<n>.fif per input when several are given)
    - out_figs/annotations.png (or annotations_<n>.png): annotation timeline
    - product.json: which rows were applied to which input, and any warnings
"""

# Copyright (c) 2026 brainlife.io
#
# This app adds annotations to MNE raw files from a table, optionally scoped per
# subject / session / task / run / tag / dataset.
#
# Author: Maximilien Chaumon (https://github.com/dnacombo)

import sys
import os
import re
import csv
import math
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'brainlife_utils'))

# Standard imports
import mne
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

# Import shared utilities
from brainlife_utils import (
    load_config,
    require_config_keys,
    setup_matplotlib_backend,
    ensure_output_dirs,
    create_product_json,
    add_info_to_product,
    add_image_to_product,
    add_raw_info_to_product,
    get_input_identities,
    match_filters,
    validate_filters,
)

# Set up matplotlib for headless execution
setup_matplotlib_backend()
mne.set_log_level('WARNING')

# Ensure output directories exist
ensure_output_dirs('out_dir', 'out_figs')

# Load configuration
config = load_config()

REQUIRED_COLUMNS = ('onset', 'duration', 'description')
FILTER_COLUMNS = ('subject', 'session', 'task', 'run', 'tag', 'dataset_id')
ALLOWED_COLUMNS = REQUIRED_COLUMNS + ('channels',) + FILTER_COLUMNS
MAX_EMBEDDED_FIGURES = 5   # keep product.json well under its 1 MB limit
MAX_LISTED_ERRORS = 15

COLOR_EXISTING = '#2a78d6'
COLOR_ADDED = '#eb6834'

product_items = []


def _fail(*messages):
    """Record each message as an error item in product.json and exit cleanly."""
    for message in messages:
        print(f"ERROR: {message}")
        add_info_to_product(product_items, message, 'error')
    create_product_json(product_items)
    sys.exit(1)


def _label(identity):
    """Human-readable name for an input (BIDS entities and tags, else file name)."""
    parts = [f"{prefix}-{identity[field]}" for field, prefix in
             (('subject', 'sub'), ('session', 'ses'), ('task', 'task'), ('run', 'run'))
             if identity.get(field)]
    if identity.get('tags'):
        parts.append('[' + ', '.join(identity['tags']) + ']')
    return ' '.join(parts) if parts else os.path.basename(identity['path'])


def _describe_filters(filters):
    return ', '.join(f"{k}={v}" for k, v in filters.items()) if filters else 'no filters'


# == VALIDATE CONFIG ==
require_config_keys(config, ['raw', 'annotations'])

fnames = config['raw']
if isinstance(fnames, str):
    fnames = [fnames]
if not (isinstance(fnames, list) and fnames and all(isinstance(f, str) and f for f in fnames)):
    _fail(f"Invalid 'raw' config value: {config['raw']!r}. Must be a file path or a non-empty list of file paths.")

mode = config.get('mode') or 'append'
if mode not in ('append', 'replace'):
    _fail(f"Invalid 'mode' config value: {mode!r}. Must be 'append' or 'replace'.")

strict = config.get('strict')
if strict is None:
    strict = False
elif isinstance(strict, str) and strict.strip().lower() in ('true', 'false'):
    strict = strict.strip().lower() == 'true'
if not isinstance(strict, bool):
    _fail(f"Invalid 'strict' config value: {config.get('strict')!r}. Must be true or false.")

table_text = config['annotations']
if not isinstance(table_text, str):
    _fail(f"Invalid 'annotations' config value: {table_text!r}. Must be a text table or a path to one.")
if os.path.isfile(table_text.strip()):
    with open(table_text.strip(), 'r') as f:
        table_text = f.read()
elif '\n' not in table_text.strip() and re.search(r'\.(tsv|csv|txt)$', table_text.strip(), re.IGNORECASE):
    _fail(f"'annotations' looks like a file name but no such file exists: {table_text.strip()!r}")

# == PARSE THE ANNOTATION TABLE ==
lines = [(n, line) for n, line in enumerate(table_text.splitlines(), start=1)
         if line.strip() and not line.lstrip().startswith('#')]
if not lines:
    _fail("The 'annotations' table is empty. Expected a header row followed by one annotation per row.")

header_line_no, header_line = lines[0]
delimiter = '\t' if '\t' in header_line else ','
header = [c.strip().lower() for c in next(csv.reader([header_line], delimiter=delimiter, skipinitialspace=True))]
bad_columns = [c for c in header if c not in ALLOWED_COLUMNS]
missing_columns = [c for c in REQUIRED_COLUMNS if c not in header]
if bad_columns or missing_columns or len(set(header)) != len(header):
    problems = []
    if missing_columns:
        problems.append(f"missing required column(s) {', '.join(missing_columns)}")
    if bad_columns:
        problems.append(f"unknown column(s) {', '.join(bad_columns)}")
    if len(set(header)) != len(header):
        problems.append("duplicate column names")
    _fail(f"Line {header_line_no} must be a header row naming the columns ({'; '.join(problems)}). "
          f"Allowed: {', '.join(ALLOWED_COLUMNS)}.")

rows = []
row_errors = []
for line_no, line in lines[1:]:
    cells = [c.strip() for c in next(csv.reader([line], delimiter=delimiter, skipinitialspace=True))]
    if len(cells) > len(header):
        row_errors.append(f"Line {line_no}: {len(cells)} cells but the header has {len(header)} columns "
                          "(quote any cell containing the delimiter, e.g. \"re:0\\d{1,2}\").")
        continue
    cells += [''] * (len(header) - len(cells))
    rec = dict(zip(header, cells))

    problems = []
    try:
        onset = float(rec['onset'])
        if not math.isfinite(onset) or onset < 0:
            problems.append(f"onset must be a number >= 0 (got {rec['onset']!r})")
    except ValueError:
        problems.append(f"onset is not a number (got {rec['onset']!r})")
    try:
        duration = float(rec['duration'])
        if not math.isfinite(duration) or duration < 0:
            problems.append(f"duration must be a number >= 0 (got {rec['duration']!r})")
    except ValueError:
        problems.append(f"duration is not a number (got {rec['duration']!r})")
    if not rec['description']:
        problems.append("description is empty")

    filters = {f: rec[f] for f in FILTER_COLUMNS if rec.get(f) not in (None, '')}
    try:
        validate_filters(filters)
    except ValueError as err:
        problems.append(str(err))

    if problems:
        row_errors.append(f"Line {line_no}: " + '; '.join(problems) + '.')
        continue
    rows.append({
        'line': line_no,
        'onset': onset,
        'duration': duration,
        'description': rec['description'],
        'channels': [c.strip() for c in re.split(r'[;|]', rec.get('channels', '')) if c.strip()],
        'filters': filters,
    })

if row_errors:
    shown = row_errors[:MAX_LISTED_ERRORS]
    if len(row_errors) > len(shown):
        shown.append(f"... and {len(row_errors) - len(shown)} more row error(s).")
    _fail(*shown)
if not rows:
    _fail("The 'annotations' table has a header but no annotation rows.")

# == MATCH ROWS TO INPUTS ==
identities = get_input_identities('config.json', 'raw')
if len(identities) != len(fnames):
    _fail(f"Could not describe the input files ({len(identities)} identities for {len(fnames)} files).")

labels = [_label(identity) for identity in identities]
applied = [[] for _ in fnames]          # rows applying to each input
unknown_fields = [{} for _ in fnames]   # per input: filter field -> table lines needing it
for row in rows:
    row['inputs'] = []
    for i, identity in enumerate(identities):
        matched, unknown = match_filters(row['filters'], identity)
        if matched:
            row['inputs'].append(i)
            applied[i].append(row)
        for field in unknown:
            unknown_fields[i].setdefault(field, []).append(row['line'])

n_applied_rows = sum(1 for row in rows if row['inputs'])
add_info_to_product(
    product_items,
    f"Read {len(rows)} annotation row(s) from the table; {n_applied_rows} apply to at least one of "
    f"{len(fnames)} input file(s) (mode: {mode}).")

problems = []   # (message) -> warning, or error when strict
for i, fields in enumerate(unknown_fields):
    for field, line_nos in fields.items():
        add_info_to_product(
            product_items,
            f"Input {i + 1} ({labels[i]}) has no '{field}' information, so table row(s) at line(s) "
            f"{', '.join(map(str, line_nos))} filtering on it were not applied to it.",
            'warning')
for row in rows:
    if not row['inputs']:
        problems.append(f"Row at line {row['line']} ({_describe_filters(row['filters'])}) "
                        f"matched none of the {len(fnames)} input file(s).")

# == LOAD INPUTS AND CHECK ROWS AGAINST THEM ==
raws = [mne.io.read_raw_fif(fname, verbose=False) for fname in fnames]
errors = []
for i, raw in enumerate(raws):
    duration_total = raw.n_times / raw.info['sfreq']
    for row in applied[i]:
        missing = [c for c in row['channels'] if c not in raw.ch_names]
        if missing:
            errors.append(f"Row at line {row['line']}: channel(s) {', '.join(missing)} not in input "
                          f"{i + 1} ({labels[i]}).")
        if row['onset'] >= duration_total:
            problems.append(f"Row at line {row['line']} starts at {row['onset']:g} s, after the end of "
                            f"input {i + 1} ({labels[i]}, {duration_total:.2f} s); it will be dropped for it.")
        elif row['onset'] + row['duration'] > duration_total:
            problems.append(f"Row at line {row['line']} ends at {row['onset'] + row['duration']:g} s, past "
                            f"the end of input {i + 1} ({labels[i]}, {duration_total:.2f} s); it will be cut.")
if errors:
    _fail(*errors[:MAX_LISTED_ERRORS])
if strict and problems:
    _fail(*problems[:MAX_LISTED_ERRORS])
for message in problems:
    add_info_to_product(product_items, message, 'warning')

# == APPLY ANNOTATIONS, SAVE, PLOT ==
for i, (fname, raw) in enumerate(zip(fnames, raws), start=1):
    rows_i = applied[i - 1]
    duration_total = raw.n_times / raw.info['sfreq']
    existing = raw.annotations
    n_existing = len(existing)
    # Existing annotations in seconds from the first sample, for plotting
    kept = [] if (mode == 'replace' and rows_i) else list(zip(
        existing.onset - raw.first_time, existing.duration, existing.description))

    if rows_i:
        new = mne.Annotations(
            onset=[r['onset'] for r in rows_i],
            duration=[r['duration'] for r in rows_i],
            description=[r['description'] for r in rows_i],
            orig_time=None,
            ch_names=[r['channels'] for r in rows_i],
        )
        if mode == 'append' and n_existing:
            # Stored onsets are relative to meas_date / file origin; new rows are
            # relative to the first sample. Bring the old ones to the same frame
            # (orig_time=None) before concatenating, then set once.
            old = mne.Annotations(
                onset=existing.onset - raw.first_time,
                duration=existing.duration,
                description=existing.description,
                orig_time=None,
                ch_names=existing.ch_names,
                extras=existing.extras,
            )
            new = old + new
        # Out-of-range rows were already reported above; MNE crops them silently here
        raw.set_annotations(new, emit_warning=False)

    n_total = len(raw.annotations)
    out_name = 'raw.fif' if len(fnames) == 1 else f'raw_{i}.fif'
    out_path = os.path.join('out_dir', out_name)
    raw.save(out_path, overwrite=True)

    # Output must reload without losing annotations. (MNE may legitimately add
    # some on reload, e.g. for its reserved BAD_ACQ_SKIP descriptions, so only a
    # shortfall is an error.)
    check = mne.io.read_raw_fif(out_path, verbose=False)
    if len(check.annotations) < n_total:
        _fail(f"Saved file {out_name} has {len(check.annotations)} annotation(s), expected at least {n_total}.")

    if rows_i:
        add_info_to_product(
            product_items,
            f"Input {i} ({labels[i - 1]}): applied {len(rows_i)} row(s) from line(s) "
            f"{', '.join(str(r['line']) for r in rows_i)}; {n_total} annotation(s) in {out_name} "
            f"({n_existing} were already in the input, mode {mode}).",
            'success')
    else:
        add_info_to_product(
            product_items,
            f"Input {i} ({labels[i - 1]}): no table row applies; saved unchanged with its "
            f"{n_existing} existing annotation(s).")
    if len(fnames) == 1:
        add_raw_info_to_product(product_items, raw)

    # Timeline: one lane per description, existing vs added
    added = [(r['onset'], r['duration'], r['description']) for r in rows_i]
    lanes = sorted({d for _, _, d in kept} | {d for _, _, d in added})
    if lanes:
        fig, ax = plt.subplots(figsize=(10, 1.2 + 0.4 * len(lanes)))
        min_width = 0.004 * duration_total   # keep zero-duration annotations visible
        for source, color in ((kept, COLOR_EXISTING), (added, COLOR_ADDED)):
            for onset, duration, description in source:
                y = lanes.index(description)
                ax.broken_barh([(onset, max(duration, min_width))], (y - 0.3, 0.6), facecolors=color)
        ax.set_yticks(range(len(lanes)))
        ax.set_yticklabels(lanes)
        ax.set_ylim(len(lanes) - 0.5, -0.5)
        ax.set_xlim(0, duration_total)
        ax.set_xlabel('Time from first sample (s)')
        ax.set_title(f"Annotations: {labels[i - 1]}", loc='left')
        for side in ('top', 'right'):
            ax.spines[side].set_visible(False)
        handles = []
        if kept:
            handles.append(Patch(facecolor=COLOR_EXISTING, label='Already in file'))
        if added:
            handles.append(Patch(facecolor=COLOR_ADDED, label='Added by this app'))
        ax.legend(handles=handles, loc='upper right', frameon=False)
        fig_name = 'annotations.png' if len(fnames) == 1 else f'annotations_{i}.png'
        fig_path = os.path.join('out_figs', fig_name)
        fig.savefig(fig_path, dpi=100, bbox_inches='tight')
        plt.close(fig)
        if i <= MAX_EMBEDDED_FIGURES:
            add_image_to_product(product_items, name=f"Annotations: {labels[i - 1]}", filepath=fig_path)

if len(fnames) > MAX_EMBEDDED_FIGURES:
    add_info_to_product(
        product_items,
        f"Showing timelines for the first {MAX_EMBEDDED_FIGURES} of {len(fnames)} inputs; "
        "all are saved in out_figs/.")

# == CREATE PRODUCT JSON ==
create_product_json(product_items)
