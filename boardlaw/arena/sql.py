import sqlite3
import pandas as pd
from contextlib import contextmanager
from pavlov import runs

DATABASE = 'output/arena.sql'

@contextmanager
def database():
    with sqlite3.connect(DATABASE) as conn:
        results_table = '''
            create table if not exists results(
                run_name text, 
                black_name text, white_name text, 
                black_wins real, white_wins real,
                moves real,
                boardsize real,
                PRIMARY KEY (run_name, black_name, white_name))'''
        conn.execute(results_table)
        yield conn

def store(run_name, result):
    if isinstance(result, list):
        for r in result:
            store(run_name, r)
        return 
    # upsert: https://stackoverflow.com/questions/2717590/sqlite-insert-on-duplicate-key-update-upsert
    with database() as conn:
        subs = (run_name, *result.names, *result.wins, result.moves, result.boardsize,
            *result.wins, result.moves)
        conn.execute('''
            insert into results 
            values (?,?,?,?,?,?,?)
            on conflict(run_name, black_name, white_name) do update set 
            black_wins = black_wins + ?,
            white_wins = white_wins + ?,
            moves = moves + ?''', subs)

def stored(run):
    with database() as c:
        return pd.read_sql_query('select * from results where run_name like ?', c, params=(f'{run}%',))

def run_counts():
    return (stored()
                .groupby('run_name')
                [['black_wins', 'white_wins']]
                .sum().sum(1))
    
def delete(run_name):
    with database() as c:
        c.execute('delete from results where run_name=?', (run_name,))

def summary(run_name):
    raw = stored(run_name)
    if len(raw) == 0:
        columns = pd.MultiIndex.from_product([['black_wins', 'white_wins',], []])
        return pd.DataFrame(columns=columns)
    df = (raw
            .groupby(['black_name', 'white_name'])
            [['black_wins', 'white_wins', 'moves']]
            .sum()
            .unstack())
    
    names = sorted(list(set(df.index) | set(df.columns.get_level_values(1))))
    df = df.reindex(index=names).reindex(columns=names, level=1)
    return df.fillna(0)

def games(run_name):
    df = summary(run_name)
    if len(df) == 0:
        df = pd.DataFrame()
        df.index.name = 'black_name'
        df.columns.name = 'white_name'
        return df
    return df.white_wins + df.black_wins

def wins(run_name, min_games=-1):
    df = summary(run_name)
    if len(df) == 0:
        return pd.DataFrame()
    return df.black_wins

def moves(run_name):
    df = summary(run_name)
    if len(df) == 0:
        return pd.DataFrame()
    return df.moves

def symmetric_games(run_name):
    g = games(run_name)
    return g + g.T

def symmetric_wins(run_name, min_games=-1):
    games = symmetric_games(run_name)
    df = summary(run_name)
    if len(df) == 0:
        return pd.DataFrame()
    return (df.black_wins + df.white_wins.T).where(games > min_games)

def symmetric_moves(run_name):
    m = moves(run_name)
    return m + m.T

def symmetric_pandas(run_name, agents=None):
    games = symmetric_games(run_name)
    wins = symmetric_wins(run_name)
    if agents is not None:
        agents = list(agents)
        games = games.reindex(index=agents, columns=agents).fillna(0)
        wins = wins.reindex(index=agents, columns=agents).fillna(0)
    return games, wins

def convert():
    from . import sql
    import json
    from pathlib import Path
    rs = [f'mohex-{n}' for n in [3, 5, 7, 9, 11]]
    for r in rs:
        js = list(sql
            .stored(r)
            .drop(['run_name', 'boardsize'], 1)
            .set_index(['black_name', 'white_name'])
            .astype(int)
            .reset_index()
            .to_dict(orient='index')
            .values())
        
        p = Path(f'output/arena/{r}.json')
        p.parent.mkdir(exist_ok=True, parents=True)
        p.write_text(json.dumps(js))