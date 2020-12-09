import time
import torch
from .. import numpy, paths, widgets, logging
import re
import numpy as np
from .. import arrdict
from . import categories
import pandas as pd
import threading
from contextlib import contextmanager
import _thread

log = logging.getLogger(__name__)

def adaptive_rule(df):
    timespan = (df.index[-1] - df.index[0]).total_seconds()
    if timespan < 600:
        return '15s'
    elif timespan < 7200:
        return '1min'
    else:
        return '10min'

def expand_columns(df, category, field):
    if isinstance(df, pd.Series):
        return pd.concat({(category, field): df}, 1)
    else:
        df = df.copy()
        df.columns = [(category, f'{field}/{c}') for c in df.columns]
        return df
    
class Reader:

    def __init__(self, run_name, prefix=''):
        self._reader = numpy.Reader(run_name, 'stats')
        self._prefix = prefix
        self._arrs = {}

    def arrays(self):
        #TODO: If this gets slow, do amortized allocation of arrays x2 as big as needed
        for channel, new in self._reader.read().items():
            category, field = re.match(r'^(.*?)/(.*)$', channel).groups()
            if field.startswith(self._prefix):
                current = [self._arrs[category, field]] if (category, field) in self._arrs else []
                self._arrs[category, field] = np.concatenate(current + new)
        return arrdict.arrdict(self._arrs)

    def pandas(self):
        arrs = self.arrays()

        dfs = {}
        for (category, field), arr in arrs.items():
            df = pd.DataFrame.from_records(arr, index='_time')
            df.index.name = 'time'
            dfs[category, field] = df
        return arrdict.arrdict(dfs)
        
    def resample(self, rule='60s', **kwargs):
        kwargs = {'rule': rule, **kwargs}

        results = []
        for (category, field), df in self.pandas().items():
            func = categories.CATEGORIES[category].resampler
            result = func(**{k: df[k] for k in df})(**kwargs)
            results.append(expand_columns(result, category, field))

        if results:
            df = pd.concat(results, 1)
            df.columns = pd.MultiIndex.from_tuples(df.columns)
            df.index = df.index - df.index[0]
            return df
        else:
            return pd.DataFrame(index=pd.TimedeltaIndex([], name='time'))

def sourceinfo(df):
    names = df.columns.get_level_values(1)
    tags = names.str.extract(r'^(?P<chart1>.*?)/(?P<label>.*)|(?P<chart2>.*)$')
    tags['category'] = df.columns.get_level_values(0)
    tags['key'] = df.columns.get_level_values(1)
    tags['title'] = tags.chart1.combine_first(tags.chart2)
    tags['id'] = tags['category'] + '_' + names
    tags.index = df.columns
    info = tags[['category', 'key', 'title', 'label', 'id']].fillna('')
    return info

def arrays(prefix='', run_name=-1):
    return Reader(run_name, prefix).arrays()

def pandas(name, run_name=-1):
    dfs = Reader(run_name, name).pandas()
    assert len(dfs) == 1, f'Got more than one dataframe back for "{name}"'
    for (_, field), df in dfs.items():
        return df
    raise KeyError(f'Couldn\'t find a statistic matching {name}')

def resample(prefix='', run_name=-1, rule='60s'):
    return Reader(run_name, prefix).resample(rule)

def tdformat(td):
    """How is this not in Python, numpy or pandas?"""
    x = td.total_seconds()
    x, _ = divmod(x, 1)
    x, s = divmod(x, 60)
    if x < 1:
        return f'{s:.0f}s'
    h, m = divmod(x, 60)
    if h < 1:
        return f'{m:.0f}m{s:02.0f}s'
    else:
        return f'{h:.0f}h{m:02.0f}m{s:02.0f}s'

def __from_dir(canceller, run_name, out, rule, throttle=1):
    run_name = paths.resolve(run_name)
    reader = Reader(run_name)
    start = pd.Timestamp.now()

    nxt = 0
    while True:
        if time.time() > nxt:
            nxt = nxt + throttle

            # Base slightly into the future, else by the time the resample actually happens you're 
            # left with an almost-empty last interval.
            base = int(time.time() % 60) + 5
            df = reader.resample(rule=rule, offset=f'{base}s')
            
            if len(df) > 0:
                final = df.ffill(limit=1).iloc[-1]
                keys, values = [], []
                for (category, _), group in sourceinfo(df).groupby(['category', 'title']):
                    formatter = categories.CATEGORIES[category]['formatter']
                    ks, vs = formatter(final, group)
                    keys.extend(ks)
                    values.extend(vs)
                key_length = max([len(str(k)) for k in keys], default=0)+1
                content = '\n'.join(f'{{:{key_length}s}} {{}}'.format(k, v) for k, v in zip(keys, values))
            else:
                content = 'No stats yet'

            size = paths.size(run_name, 'stats')
            age = pd.Timestamp.now() - start
            out.refresh(f'{run_name}: {tdformat(age)} old, {rule} rule, {size:.0f}MB on disk\n\n{content}')

        if canceller.is_set():
            break

        time.sleep(.1)

def _from_dir(*args, **kwargs):
    try:
        __from_dir(*args, **kwargs)
    except KeyboardInterrupt:
        log.info('Interrupting main')
        _thread.interrupt_main()

@contextmanager
def from_dir(run_name, compositor=None, rule='60s'):
    if logging.in_ipython():
        try:
            canceller = threading.Event()
            out = (compositor or widgets.Compositor()).output()
            thread = threading.Thread(target=_from_dir, args=(canceller, run_name, out, rule))
            thread.start()
            yield
        finally:
            canceller.set()
            thread.join(1)
            if thread.is_alive():
                log.error('Stat display thread won\'t die')
            else:
                log.info('Stat display thread cancelled')

            # Want to leave the outputs open so you can see the final stats
            # out.close()
    else:
        log.info('No stats emitted in console mode')
        yield