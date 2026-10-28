"""Real extractor protocol and bank/cache tests with only model calls mocked."""
from __future__ import annotations
import importlib.util
import io
import json
import os
import subprocess
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import bank
import extract
import support

DIRECTORY = Path(__file__).parent / 'extractors/vocal-contour'
spec = importlib.util.spec_from_file_location('extract_contour', DIRECTORY / 'extract_contour.py')
program = importlib.util.module_from_spec(spec)
spec.loader.exec_module(program)

FAKE = r'''#!{python}
import importlib.util, os, sys
if os.path.basename(sys.argv[1]) != 'extract_contour.py':
    os.execv({fallback}, [{fallback}, *sys.argv[1:]])
spec = importlib.util.spec_from_file_location('extract_contour', sys.argv[1])
p = importlib.util.module_from_spec(spec); spec.loader.exec_module(p)
import numpy as np
class Models:
    software = [dict(name='mock-acoustic-models',version='1')]
    def __init__(self, config): pass
    def pitch(self, audio):
        n = len(audio)//160+1
        return np.full(n,440.),np.full(n,.9),np.full(n,440.)
    def boundaries(self, audio): return [1., 2., 6., 12.]
p.Models = Models
# Only external provisioning checks are bypassed; real decode/window/core/write.
p.settings = lambda values: values
sys.exit(p.main(sys.argv[2:]))
'''


def fake_python(directory: Path, fallback: Path | None = None) -> Path:
    p = directory / 'fake-vocal-python'
    p.write_text(FAKE.replace('{python}', sys.executable).replace('{fallback}', repr(str(fallback or sys.executable))))
    p.chmod(0o755)
    return p


@support.needs_ffmpeg
class VocalExtractor(unittest.TestCase):
    def test_registered_extractor_tail_coverage_typed_features_and_cache_hit(self):
        directory = Path(tempfile.mkdtemp(prefix='bank-vocal-'))
        self.addCleanup(shutil.rmtree, directory, True)
        # Crossing20s exercises separate contexts and a partial final target.
        audio = support.sine_wav(directory / 'tone.wav', 16000, '20.075')
        argv = ['run','vocal-contour','--content',str(audio),'--bank',str(directory/'bank'),
                '--program',f'f0-python={fake_python(directory)}']
        out = io.StringIO()
        with redirect_stdout(out):
            code = extract.main(argv)
        self.assertEqual(code, 0, out.getvalue())
        entry = bank.Bank(directory/'bank').entry(bank.file_digest(audio), 'vocal-contour')
        self.assertEqual(entry.series('pitch').shape, (2008,1))
        self.assertEqual(entry.series('held').shape, (2008,1))
        self.assertTrue(entry.items('notes'))
        self.assertGreater(entry.scalar('voiced_fraction'), .5)
        provenance = json.loads((entry.path/'provenance.json').read_text())
        self.assertEqual(len(provenance['windows']), 2)
        self.assertEqual(provenance['windows'][0]['boundary_context'][0], 0)
        self.assertIn('uncertain_regions', provenance['windows'][0]['note_reading'])
        tail = provenance['windows'][1]
        self.assertEqual(tail['samples'], 1200)
        self.assertEqual(tail['pitch_context_decoded_samples'], 1200)
        self.assertEqual(tail['pitch_padded_samples'], 480000)
        self.assertNotEqual(tail['pitch_decoded_pcm_sha256'], tail['pitch_pcm_sha256'])
        self.assertEqual(provenance['recipe']['tail_context'], 'zero-pad-to-context')
        self.assertLessEqual(max(n['end'] for n in entry.items('notes')),20.075)
        with patch.object(extract.subprocess, 'Popen', side_effect=AssertionError('cache reran process')):
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(extract.main(argv), 0)
            self.assertEqual(json.loads(out.getvalue())['extractors']['vocal-contour']['status'], 'hit')

    def test_import_preserves_matcher_thread_policy_and_does_not_load_numpy(self):
        # Start without NumPy or cover imported: adapter discovery must not
        # choose a numerical runtime for unrelated commands in the process.
        script = """
import importlib.util, os, sys
from pathlib import Path
path = Path('extractors/vocal-contour/extract_contour.py')
spec = importlib.util.spec_from_file_location('extract_contour', path)
program = importlib.util.module_from_spec(spec)
spec.loader.exec_module(program)
assert 'numpy' not in sys.modules, 'adapter import eagerly loaded NumPy'
for key in ('OPENBLAS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMBA_NUM_THREADS'):
    assert os.environ[key] == '1', (key, os.environ[key])
import cover
assert cover.blas_runtime().endswith(', 1 thread'), cover.blas_runtime()
assert cover.software()
print('one-thread matcher preserved')
"""
        env = dict(os.environ)
        for key in ('OPENBLAS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMBA_NUM_THREADS'):
            env[key] = '1'
        completed = subprocess.run([sys.executable, '-c', script], cwd=DIRECTORY.parents[1],
                                   env=env, text=True, capture_output=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), 'one-thread matcher preserved')

    def test_cli_selects_two_threads_without_import_time_model_loading(self):
        script = """
import os, runpy, sys
sys.argv = ['extract_contour.py']
try:
    runpy.run_path('extractors/vocal-contour/extract_contour.py', run_name='__main__')
except SystemExit as exc:
    assert exc.code == 1  # No request: usage fails before model loading.
for key in ('OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'NUMBA_NUM_THREADS'):
    assert os.environ[key] == '2', (key, os.environ[key])
assert 'numpy' not in sys.modules
print('two-thread CLI policy preserved')
"""
        completed = subprocess.run([sys.executable, '-c', script], cwd=DIRECTORY.parents[1],
                                   text=True, capture_output=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), 'two-thread CLI policy preserved')

    def test_operator_model_configuration_fails_closed(self):
        with self.assertRaisesRegex(ValueError, 'configure'):
            program.settings(program.parameters())
        changed = {**program.parameters(), 'target_seconds': 10}
        with self.assertRaisesRegex(ValueError, 'fixed recipe'):
            program.settings(changed)

    def test_model_code_pin_changes_with_source(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory); (p/'model.py').write_text('a=1\n')
            first=program.code_digest(p)
            (p/'cache.bin').write_bytes(b'ignored')
            self.assertEqual(first, program.code_digest(p))
            (p/'model.py').write_text('a=2\n')
            self.assertNotEqual(first, program.code_digest(p))

if __name__ == '__main__':
    unittest.main()
