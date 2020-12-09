import numpy as np
from numpy.lib import format as npformat
from . import paths
from io import BytesIO
from datetime import datetime
from collections import defaultdict
from pathlib import Path
import time

def infer_dtype(exemplar):
    return np.dtype([(k, v.dtype if isinstance(v, np.generic) else type(v)) for k, v in exemplar.items()])

def make_header(dtype):
    """
    Ref: https://numpy.org/devdocs/reference/generated/numpy.lib.format.html
    We're doing version 3. Only difference is the zero shape, since we're
    going to deduce the array size from the filesize.
    """
    assert not dtype.hasobject, 'Arrays with objects in get pickled, so can\'t be appended to'

    bs = BytesIO()
    npformat._write_array_header(bs, {
        'descr': dtype.descr, 
        'fortran_order': False, 
        'shape': (0,)},
        version=(3, 0))
    return bs.getvalue()

class FileWriter:

    def __init__(self, path, period=5):
        self._path = Path(path) if isinstance(path, str) else path
        self._file = None
        self._period = period 
        self._next = time.time()
        
    def _init(self, exemplar):
        self._file = self._path.open('wb', buffering=4096)
        self._dtype = infer_dtype(exemplar)
        self._file.write(make_header(self._dtype))
        self._file.flush()

    def write(self, d):
        if self._file is None:
            self._init(d)
        assert set(d) == set(self._dtype.names)
        row = np.array([tuple(v for v in d.values())], self._dtype)
        self._file.write(row.tobytes())
        self._file.flush()

    def close(self):
        self._file.close()
        self._file = None

class Writer:

    def __init__(self, run_name, group):
        self._run_name = run_name
        self._group = group
        self._writers = {}

    def write(self, channel, d):
        if channel not in self._writers:
            path = paths.process_path(self._run_name, self._group, channel).with_suffix('.npr')
            self._writers[channel] = FileWriter(path)
        self._writers[channel].write(d)

    def write_many(self, ds):
        for channel, d in ds.items():
            if channel not in self._writers:
                path = paths.process_path(self._run_name, self._group, channel).with_suffix('.npr')
                self._writers[channel] = FileWriter(path)
            self._writers[channel].write(d)

    def close(self):
        for _, w in self._writers.items():
            w.close()
        self._writers = {}

class FileReader:

    def __init__(self, path):
        self._path = Path(path) if isinstance(path, str) else path
        self._file = None

    def _init(self):
        #TODO: Can speed this up with PAG's regex header parser
        self._file = self._path.open('rb')
        version = npformat.read_magic(self._file)
        _, _, dtype = npformat._read_array_header(self._file, version)
        self._dtype = dtype

    def read(self):
        if self._file is None:
            self._init()
        return np.fromfile(self._file, dtype=self._dtype)

    def close(self):
        self._file.close()
        self._file = None

class Reader:

    def __init__(self, run_name, group):
        self._run_name = paths.resolve(run_name)
        self._group = group
        self._readers = {}

    def read(self):
        for path in paths.subdir(self._run_name, self._group).glob('**/*.npr'):
            parsed = paths.parse(path)
            channel = '/'.join(parsed.parts[1:])
            filename = parsed.filename.split('.')[0]
            if (channel, filename) not in self._readers:
                self._readers[channel, filename] = FileReader(path)

        results = defaultdict(lambda: [])
        for (channel, _), reader in self._readers.items():
            arr = reader.read()
            if len(arr) > 0:
                results[channel].append(arr)

        return results


def test_file_write_read():
    d = {'total': 65536, 'count': 14, '_time': np.datetime64('now')}
    
    paths.clear('test', 'stats')
    path = paths.process_path('test', 'stats', 'mean/traj-length').with_suffix('.npr')

    writer = FileWriter(path)
    writer.write(d)

    reader = FileReader(path)
    r = reader.read()

    assert len(r) == 1

def test_write_read():
    paths.clear('test', 'stats')

    writer = Writer('test', 'stats')
    writer.write('mean/traj-length', {'total': 65536, 'count': 14, '_time': np.datetime64('now')})
    writer.write('max/reward', {'total': 50000.5, 'count': 50, '_time': np.datetime64('now')})

    reader = Reader('test', 'stats')
    r = reader.read()

    assert len(r) == 2