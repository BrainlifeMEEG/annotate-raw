"""End-to-end tests for annotate-raw, using synthetic recordings only."""

import datetime
import json
import os
import shutil
import subprocess
import sys

import mne
import numpy as np
import pytest

APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
CH_NAMES = ['MEG0111', 'MEG0112', 'EEG001']
SFREQ = 100.0
DURATION = 60.0
HEADER = 'subject,run,tag,dataset_id,onset,duration,description,channels'


def make_raw(path, meas_date=True, first_samp=0, annotations=None):
    rng = np.random.default_rng(0)
    info = mne.create_info(CH_NAMES, SFREQ, ['mag', 'mag', 'eeg'])
    data = rng.standard_normal((len(CH_NAMES), int(DURATION * SFREQ))) * 1e-12
    raw = mne.io.RawArray(data, info, first_samp=first_samp, verbose=False)
    if meas_date:
        raw.set_meas_date(datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc))
    if annotations is not None:
        raw.set_annotations(annotations)
    raw.save(str(path), overwrite=True, verbose=False)
    return str(path)


def entry(subject=None, run=None, tags=(), dataset_id=None, input_id='raw'):
    meta = {k: v for k, v in (('subject', subject), ('run', run)) if v is not None}
    e = {'id': input_id, 'tags': list(tags), 'datatype_tags': [], 'meta': meta}
    if dataset_id:
        e['dataset_id'] = dataset_id
    return e


def run_app(tmp_path, cfg):
    (tmp_path / 'config.json').write_text(json.dumps(cfg))
    proc = subprocess.run([sys.executable, os.path.join(APP_DIR, 'main.py')],
                          cwd=tmp_path, capture_output=True, text=True)
    product = json.loads((tmp_path / 'product.json').read_text())['brainlife']
    msgs = [(i['type'], i['msg']) for i in product if 'msg' in i]
    return proc, msgs


def annots(path):
    """(onset from first sample, duration, description, ch_names) per annotation, sorted."""
    raw = mne.io.read_raw_fif(str(path), verbose=False)
    a = raw.annotations
    return sorted((round(float(o - raw.first_time), 3), float(d), str(s), tuple(c))
                  for o, d, s, c in zip(a.onset, a.duration, a.description, a.ch_names))


def messages(msgs, kind=None):
    return [m for t, m in msgs if kind is None or t == kind]


@pytest.fixture
def two_inputs(tmp_path):
    a = make_raw(tmp_path / 'a.fif')
    b = make_raw(tmp_path / 'b.fif')
    inputs = [entry('01', '1', ['rest'], 'a' * 24), entry('02', '2', ['task', 'Run2'], 'b' * 24)]
    return [a, b], inputs


def cfg_for(files, inputs, table, **extra):
    cfg = {'raw': files if len(files) > 1 else files[0], 'annotations': table, **extra}
    if inputs is not None:
        cfg['_inputs'] = inputs
    return cfg


# -- basics ---------------------------------------------------------------

def test_single_input_no_filters(tmp_path):
    f = make_raw(tmp_path / 'raw.fif')
    proc, msgs = run_app(tmp_path, {'raw': f, 'annotations': 'onset,duration,description\n12.5,2,bad_muscle'})
    assert proc.returncode == 0, proc.stderr
    assert annots(tmp_path / 'out_dir' / 'raw.fif') == [(12.5, 2.0, 'bad_muscle', ())]
    assert (tmp_path / 'out_figs' / 'annotations.png').exists()
    assert any('applied 1 row' in m for m in messages(msgs, 'success'))


def test_annotation_frame_preserved_with_first_samp(tmp_path):
    """Onsets are seconds from the first sample, with or without meas_date, and
    when the recording does not start at sample 0."""
    for meas_date in (True, False):
        f = make_raw(tmp_path / 'raw.fif', meas_date=meas_date, first_samp=1234)
        proc, _ = run_app(tmp_path, {'raw': f, 'annotations': 'onset,duration,description\n12.5,2,x'})
        assert proc.returncode == 0, proc.stderr
        assert annots(tmp_path / 'out_dir' / 'raw.fif') == [(12.5, 2.0, 'x', ())]
        # and appending keeps both in the same frame
        f2 = str(tmp_path / 'second_pass.fif')
        shutil.copy(tmp_path / 'out_dir' / 'raw.fif', f2)
        proc, _ = run_app(tmp_path, {'raw': f2, 'annotations': 'onset,duration,description\n30,1,y'})
        assert proc.returncode == 0, proc.stderr
        assert annots(tmp_path / 'out_dir' / 'raw.fif') == [(12.5, 2.0, 'x', ()), (30.0, 1.0, 'y', ())]


def test_hyphenated_description_and_multiple_channels(tmp_path):
    f = make_raw(tmp_path / 'raw.fif')
    table = 'onset,duration,description,channels\n5,1,bad-muscle,MEG0111;MEG0112\n7,1,bad_eeg,EEG001'
    proc, _ = run_app(tmp_path, {'raw': f, 'annotations': table})
    assert proc.returncode == 0, proc.stderr
    assert annots(tmp_path / 'out_dir' / 'raw.fif') == [
        (5.0, 1.0, 'bad-muscle', ('MEG0111', 'MEG0112')), (7.0, 1.0, 'bad_eeg', ('EEG001',))]


def test_tab_delimiter_comments_and_blank_lines(tmp_path):
    f = make_raw(tmp_path / 'raw.fif')
    table = '# my annotations\n\nonset\tduration\tdescription\n# note\n1\t1\ta\n\n3\t1\tb\n'
    proc, _ = run_app(tmp_path, {'raw': f, 'annotations': table})
    assert proc.returncode == 0, proc.stderr
    assert [a[2] for a in annots(tmp_path / 'out_dir' / 'raw.fif')] == ['a', 'b']


def test_table_from_file_path(tmp_path):
    f = make_raw(tmp_path / 'raw.fif')
    (tmp_path / 'table.tsv').write_text('onset\tduration\tdescription\n4\t1\tfrom_file\n')
    proc, _ = run_app(tmp_path, {'raw': f, 'annotations': str(tmp_path / 'table.tsv')})
    assert proc.returncode == 0, proc.stderr
    assert annots(tmp_path / 'out_dir' / 'raw.fif') == [(4.0, 1.0, 'from_file', ())]


# -- filters on multiple inputs ---------------------------------------------

def out(tmp_path, n):
    return tmp_path / 'out_dir' / f'raw_{n}.fif'


def test_no_filters_applies_to_every_input(tmp_path, two_inputs):
    files, inputs = two_inputs
    proc, _ = run_app(tmp_path, cfg_for(files, inputs, 'onset,duration,description\n10,1,all'))
    assert proc.returncode == 0, proc.stderr
    assert annots(out(tmp_path, 1)) == annots(out(tmp_path, 2)) == [(10.0, 1.0, 'all', ())]


@pytest.mark.parametrize('cell, expected', [
    ('01', [1]), ('1', [1]), ('sub-02', [2]), ('0*', [1, 2]), ('0[2-9]', [2]),
    ('re:0[12]', [1, 2]), ('re:02', [2]), ('"re:0\\d{1,2}"', [1, 2]),
])
def test_subject_filter_styles(tmp_path, two_inputs, cell, expected):
    files, inputs = two_inputs
    table = f'subject,onset,duration,description\n{cell},10,1,hit'
    proc, _ = run_app(tmp_path, cfg_for(files, inputs, table))
    assert proc.returncode == 0, proc.stderr
    for n in (1, 2):
        got = annots(out(tmp_path, n))
        assert got == ([(10.0, 1.0, 'hit', ())] if n in expected else [])


def test_run_zero_padding_and_question_mark(tmp_path, two_inputs):
    files, inputs = two_inputs
    table = 'run,onset,duration,description\n01,10,1,a\n?,20,1,b\n[2],30,1,c'
    proc, _ = run_app(tmp_path, cfg_for(files, inputs, table))
    assert proc.returncode == 0, proc.stderr
    assert [a[2] for a in annots(out(tmp_path, 1))] == ['a', 'b']
    assert [a[2] for a in annots(out(tmp_path, 2))] == ['b', 'c']


def test_tag_and_list_and_dataset_id_pin(tmp_path, two_inputs):
    files, inputs = two_inputs
    table = ('tag,dataset_id,onset,duration,description\n'
             'task;run2,,10,1,tags\n'
             f',{"a" * 24},20,1,pinned\n'
             'rest;run2,,30,1,never')
    proc, msgs = run_app(tmp_path, cfg_for(files, inputs, table))
    assert proc.returncode == 0, proc.stderr
    assert [a[2] for a in annots(out(tmp_path, 1))] == ['pinned']
    assert [a[2] for a in annots(out(tmp_path, 2))] == ['tags']
    assert any('line 4' in m and 'matched none' in m for m in messages(msgs, 'warning'))


def test_unmatched_row_warns_then_errors_under_strict(tmp_path, two_inputs):
    files, inputs = two_inputs
    table = 'subject,onset,duration,description\n99,10,1,nobody'
    proc, msgs = run_app(tmp_path, cfg_for(files, inputs, table))
    assert proc.returncode == 0, proc.stderr
    assert any('matched none' in m for m in messages(msgs, 'warning'))
    (tmp_path / 'out_dir' / 'raw_1.fif').unlink()
    proc, msgs = run_app(tmp_path, cfg_for(files, inputs, table, strict=True))
    assert proc.returncode == 1
    assert any('matched none' in m for m in messages(msgs, 'error'))
    assert not (tmp_path / 'out_dir' / 'raw_1.fif').exists()


def test_missing_metadata_never_matches_and_warns(tmp_path, two_inputs):
    files, _ = two_inputs
    table = 'subject,onset,duration,description\n01,10,1,x'
    proc, msgs = run_app(tmp_path, cfg_for(files, None, table))   # no _inputs, non-BIDS names
    assert proc.returncode == 0, proc.stderr
    assert annots(out(tmp_path, 1)) == annots(out(tmp_path, 2)) == []
    assert any("no 'subject' information" in m for m in messages(msgs, 'warning'))


def test_bids_filename_fallback_without_inputs(tmp_path):
    f = make_raw(tmp_path / 'sub-05_task-rest_run-02_meg.fif')
    table = 'subject,task,run,onset,duration,description\n5,rest,2,10,1,found'
    proc, _ = run_app(tmp_path, {'raw': f, 'annotations': table})
    assert proc.returncode == 0, proc.stderr
    assert annots(tmp_path / 'out_dir' / 'raw.fif') == [(10.0, 1.0, 'found', ())]


# -- append vs replace -------------------------------------------------------

def existing_annotations():
    return mne.Annotations([3.0], [1.0], ['old'], orig_time=None)


def test_append_keeps_existing_replace_discards_for_matching_inputs_only(tmp_path):
    files = [make_raw(tmp_path / n, annotations=existing_annotations()) for n in ('a.fif', 'b.fif')]
    inputs = [entry('01'), entry('02')]
    table = 'subject,onset,duration,description\n01,10,1,new'

    proc, _ = run_app(tmp_path, cfg_for(files, inputs, table))   # default: append
    assert proc.returncode == 0, proc.stderr
    assert [a[2] for a in annots(out(tmp_path, 1))] == ['old', 'new']
    assert [a[2] for a in annots(out(tmp_path, 2))] == ['old']

    proc, _ = run_app(tmp_path, cfg_for(files, inputs, table, mode='replace'))
    assert proc.returncode == 0, proc.stderr
    assert [a[2] for a in annots(out(tmp_path, 1))] == ['new']       # replaced
    assert [a[2] for a in annots(out(tmp_path, 2))] == ['old']       # untouched: no row applies


# -- validation and clean failures ------------------------------------------

def test_missing_annotations_key_exits_cleanly(tmp_path):
    f = make_raw(tmp_path / 'raw.fif')
    proc, msgs = run_app(tmp_path, {'raw': f})
    assert proc.returncode == 1 and 'Traceback' not in proc.stderr
    assert any('annotations' in m for m in messages(msgs, 'error'))


@pytest.mark.parametrize('table, fragment', [
    ('2,2,bibi', 'header row'),                                         # no header
    ('onset,duration\n1,1', 'missing required column'),
    ('onset,duration,description,color\n1,1,a,red', 'unknown column'),
    ('onset,duration,description\nsoon,1,a', 'onset is not a number'),
    ('onset,duration,description\n-1,1,a', 'onset must be'),
    ('onset,duration,description\n1,-2,a', 'duration must be'),
    ('onset,duration,description\n1,1,', 'description is empty'),
    ('subject,onset,duration,description\nre:0\\d{1,2},1,1,a', 'cells but the header'),   # unquoted comma
    ('subject,onset,duration,description\nre:(open,1,1,a', 'invalid regular expression'),
    ('subject,onset,duration,description\n"re:(open",1,1,a', 'invalid regular expression'),
    ('onset,duration,description\n1,1,a,extra', 'cells but the header'),
    ('onset,duration,description', 'no annotation rows'),
    ('   ', 'empty'),
    ('annotations.tsv', 'no such file'),
])
def test_table_errors_are_clean(tmp_path, table, fragment):
    f = make_raw(tmp_path / 'raw.fif')
    proc, msgs = run_app(tmp_path, {'raw': f, 'annotations': table})
    assert proc.returncode == 1 and 'Traceback' not in proc.stderr, proc.stderr
    assert any(fragment in m for m in messages(msgs, 'error')), msgs
    assert not (tmp_path / 'out_dir' / 'raw.fif').exists()


def test_invalid_mode_and_strict(tmp_path):
    f = make_raw(tmp_path / 'raw.fif')
    table = 'onset,duration,description\n1,1,a'
    for extra, fragment in (({'mode': 'merge'}, "'mode'"), ({'strict': 'maybe'}, "'strict'")):
        proc, msgs = run_app(tmp_path, {'raw': f, 'annotations': table, **extra})
        assert proc.returncode == 1 and 'Traceback' not in proc.stderr
        assert any(fragment in m for m in messages(msgs, 'error'))


def test_unknown_channel_is_an_error(tmp_path):
    f = make_raw(tmp_path / 'raw.fif')
    proc, msgs = run_app(tmp_path, {'raw': f, 'annotations': 'onset,duration,description,channels\n1,1,a,MEG9999'})
    assert proc.returncode == 1 and 'Traceback' not in proc.stderr
    assert any('MEG9999' in m for m in messages(msgs, 'error'))


def test_out_of_range_rows_warn_then_error_under_strict(tmp_path):
    f = make_raw(tmp_path / 'raw.fif')
    table = 'onset,duration,description\n58,5,partly_out\n70,1,fully_out\n10,1,ok'
    proc, msgs = run_app(tmp_path, {'raw': f, 'annotations': table})
    assert proc.returncode == 0, proc.stderr
    warns = messages(msgs, 'warning')
    assert any('line 2' in m and 'cut' in m for m in warns)
    assert any('line 3' in m and 'dropped' in m for m in warns)
    got = annots(tmp_path / 'out_dir' / 'raw.fif')
    assert [a[2] for a in got] == ['ok', 'partly_out']       # fully_out omitted, partly_out clipped
    assert got[1][1] == pytest.approx(2.0, abs=0.02)

    (tmp_path / 'out_dir' / 'raw.fif').unlink()
    proc, msgs = run_app(tmp_path, {'raw': f, 'annotations': table, 'strict': True})
    assert proc.returncode == 1
    assert not (tmp_path / 'out_dir' / 'raw.fif').exists()


def test_output_reloads_and_filename_convention(tmp_path, two_inputs):
    files, inputs = two_inputs
    proc, _ = run_app(tmp_path, cfg_for(files, inputs, 'onset,duration,description\n1,1,a'))
    assert proc.returncode == 0, proc.stderr
    for n in (1, 2):
        mne.io.read_raw_fif(str(out(tmp_path, n)), verbose=False)
    assert not (tmp_path / 'out_dir' / 'raw.fif').exists()
    assert (tmp_path / 'out_figs' / 'annotations_1.png').exists()
