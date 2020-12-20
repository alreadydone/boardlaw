import time
import numpy as np
import torch
from rebar import arrdict, stats, recording, storing, widgets, logging
from logging import getLogger
from . import arena

log = getLogger(__name__)

@torch.no_grad()
def rollout(worlds, agents, n_steps=None, n_trajs=None, n_reps=None):
    assert sum(x is not None for x in (n_steps, n_trajs, n_reps)) == 1, 'Must specify exactly one of n_steps or n_trajs or n_reps'

    trace = []
    steps, trajs = 0, 0
    reps = torch.zeros(worlds.n_envs, device=worlds.device)
    while True:
        actions = torch.full((worlds.n_envs,), -1, device=worlds.device)
        for i, agent in enumerate(agents):
            mask = worlds.seats == i
            if mask.any():
                actions[mask] = agent(worlds[mask]).actions
        worlds, transitions = worlds.step(actions)
        trace.append(arrdict.arrdict(
            actions=actions,
            transitions=transitions,
            worlds=worlds))
        steps += 1
        if n_steps and (steps >= n_steps):
            break
        trajs += transitions.terminal.sum()
        if n_trajs and (trajs >= n_trajs):
            break
        reps += transitions.terminal
        if n_reps and (reps >= n_reps).all():
            break
    return arrdict.stack(trace)

def plot_all(f):

    def proxy(state):
        import numpy as np
        import matplotlib.pyplot as plt

        B = state.seats.shape[0]
        assert B < 65, f'Plotting {B} traces will be prohibitively slow' 
        n_rows = int(B**.5)
        n_cols = int(np.ceil(B/n_rows))
        fig, axes = plt.subplots(n_rows, n_cols, sharex=True, sharey=True, squeeze=False)

        for e in range(B):
            f(state, e, ax=axes.flatten()[e])
        
        return fig
    return proxy

def record_worlds(worlds, N=0):
    state = arrdict.numpyify(worlds)
    with recording.ParallelEncoder(plot_all(worlds.plot_worlds), N=N, fps=1) as encoder:
        for i in range(state.board.shape[0]):
            encoder(state[i])
    return encoder
    
def record(world, agents, N=0, **kwargs):
    trace = rollout(world, agents, **kwargs)
    return record_worlds(trace.worlds, N=N)

def demo(run_name=-1):
    from boardlaw import mohex
    from .main.common import worldfunc, agentfunc

    n_envs = 4
    world = worldfunc(n_envs, device='cuda:1')
    agent = agentfunc(device='cuda:1')
    agent.load_state_dict(storing.select(storing.load_latest(run_name), 'agent'))
    mhx = mohex.MoHexAgent(presearch=False, max_games=1)
    record(world, [agent, agent], n_reps=1, N=0).notebook()

def compare(fst_run=-1, snd_run=-1, n_envs=256, device='cuda:1'):
    import pandas as pd
    from .main.common import worldfunc, agentfunc

    world = worldfunc(n_envs, device=device)

    fst = agentfunc(device=device)
    fst.load_state_dict(storing.select(storing.load_latest(fst_run), 'agent'))

    snd = agentfunc(device=device)
    snd.load_state_dict(storing.select(storing.load_latest(snd_run), 'agent'))

    bw = rollout(world, [fst, snd], n_reps=1)
    bw_wins = (bw.transitions.rewards[bw.transitions.terminal.cumsum(0) <= 1] == 1).sum(0)

    wb = rollout(world, [snd, fst], n_reps=1)
    wb_wins = (wb.transitions.rewards[wb.transitions.terminal.cumsum(0) <= 1] == 1).sum(0)

    # Rows: black, white; cols: old, new
    wins = torch.stack([bw_wins, wb_wins.flipud()]).detach().cpu().numpy()

    return pd.DataFrame(wins/n_envs, ['black', 'white'], ['fst', 'snd'])

def demo_record():
    from boardlaw import mohex, analysis
    from .main.common import worldfunc, agentfunc

    n_envs = 16
    world = worldfunc(n_envs)
    agent = agentfunc()
    mhx = mohex.MoHexAgent()
    analysis.record(world, [agent, mhx], n_reps=1, N=0).notebook()

def demo_rollout():
    from . import networks, mcts, mohex
    env = hex.Hex.initial(n_envs=4, boardsize=5, device='cuda')
    network = networks.Network(env.obs_space, env.action_space, width=128).to(env.device)
    agent = mcts.MCTSAgent(env, network, n_nodes=16)
    oppo = mohex.MoHexAgent(env)

    trace = rollout(env, [agent, oppo], 20)

    trace.responses.rewards.sum(0).sum(0)

def monitor(run_name=-1):
    from .main.common import worldfunc, agentfunc
    compositor = widgets.Compositor()
    with logging.from_dir(run_name, compositor), stats.from_dir(run_name, compositor), \
            arena.monitor(run_name, worldfunc, agentfunc):
        while True:
            time.sleep(1)
