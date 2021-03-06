import fnmatch
import numpy as np
import pandas as pd
import threading
from contextlib import contextmanager
from . import timeseries
from .. import runs, files
import aljpy
import re

KINDS = {
    **timeseries.KINDS}

T = threading.local()
T.WRITERS = {}
T.RUN = None

# channel: label or group.la.bel
# prefix: stats.channel
# filename: prefix.3.npr
PREFIX = r'(?P<origin>.*?)\.(?P<channel>.*)'
INDEXED_FILENAME = r'(?P<prefix>.*)\.(?P<idx>\d+)\.(?P<ext>.*)'
FILENAME = r'(?P<prefix>.*)\.(?P<ext>.*)'

@contextmanager
def to_run(run):
    if run is None:
        yield
        return 

    run = runs.resolve(run)
    try:
        if hasattr(T, 'run'):
            raise ValueError('Run already set')
        T.WRITERS = {}
        T.RUN = run
        yield
    finally:
        del T.WRITERS
        del T.RUN

def run():
    return T.RUN if hasattr(T, 'RUN') else None

def writer(prefix, factory=None):
    if factory is not None:
        if prefix not in T.WRITERS:
            T.WRITERS[prefix] = factory()
    return T.WRITERS[prefix]

def make_prefix(channel):
    return f'stats.{channel}'

def parse_channel(channel):
    parts = channel.split('.')
    if len(parts) == 1:
        return aljpy.dotdict(group=parts[0], label='')
    else:
        return aljpy.dotdict(group=parts[0], label='.'.join(parts[1:]))

def parse_prefix(prefix):
    p = re.fullmatch(PREFIX, prefix).groupdict()
    return aljpy.dotdict(**p, **parse_channel(p['channel']))

def parse_filename(filename):
    p = re.fullmatch(INDEXED_FILENAME, filename)
    if not p:
        p = re.fullmatch(FILENAME, filename)
    p = p.groupdict()
    return aljpy.dotdict(**p, **parse_prefix(p['prefix']))

class StatsReaders:

    def __init__(self, run, channel='*'):
        self._run = run
        self._channel = channel
        self._pool = {}
        self.refresh()

    def refresh(self):
        for filename, info in files.files(self._run).items():
            if files.origin(filename) == 'stats':
                parts = parse_filename(filename)
                kind = info['kind']
                match = fnmatch.fnmatch(parts.channel, self._channel)
                if (kind in KINDS) and match and (parts.prefix not in self._pool):
                    reader = KINDS[kind].reader(self._run, parts.prefix)
                    self._pool[parts.prefix] = reader

    #TODO: Just inherit from dict, c'mon
    def __getitem__(self, prefix):
        return self._pool[prefix]

    def __iter__(self):
        return iter(self._pool)

    def items(self):
        return self._pool.items()
        
def reader(run, channel):
    #TODO: This won't generalise!
    prefix = make_prefix(channel)
    exemplars = [f for f in runs.info(run)['_files'] if f.startswith(prefix)]
    if not exemplars:
        raise IOError(f'Run "{run}" has no "{channel}" files')
    kind = files.info(run, exemplars[0])['kind']
    reader = KINDS[kind].reader(run, prefix)
    return reader

def exists(run, channel):
    prefix = make_prefix(channel)
    exemplar = f'{prefix}.0.npr'
    return files.exists(run, exemplar)
