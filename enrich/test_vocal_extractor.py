"""Real extractor protocol and bank/cache tests with only model calls mocked."""
from __future__ import annotations
import importlib.util
import io
import json
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
