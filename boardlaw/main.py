import gc
import time
import numpy as np
import torch
from rebar import arrdict, profiling, pickle
from pavlov import stats, logs, runs, storage, archive
from . import hex, mcts, networks, learning, validation, analysis, arena, leagues
from torch.nn import functional as F
from logging import getLogger

log = getLogger(__name__)

@torch.no_grad()
def chunk_stats(chunk, n_new):
    with stats.defer():
        tail = chunk[-n_new:]
        d, t = tail.decisions, tail.transitions
        n_trajs = t.terminal.sum()
        n_inputs = t.terminal.size(0)
        n_samples = t.terminal.nelement()
        n_sims = d.n_sims.int().sum()
        stats.rate('sample-rate.actor', n_samples)
        stats.mean('traj-length', n_samples, n_trajs)
        stats.cumsum('count.traj', n_trajs)
        stats.cumsum('count.inputs', n_inputs)
        stats.cumsum('count.chunks', 1)
        stats.cumsum('count.samples', n_samples)
        stats.cumsum('count.sims', n_sims)
        stats.rate('step-rate.chunks', 1)
        stats.rate('step-rate.inputs', n_inputs)
        stats.rate('sim-rate', n_sims)
        stats.mean('mcts-n-leaves', d.n_leaves.float().mean())

        wins = (t.rewards == 1).sum(0).sum(0)
        for i, w in enumerate(wins):
            stats.mean(f'wins.seat-{i}', w, n_trajs)

        d, t = chunk.decisions, chunk.transitions
        v = d.v[t.terminal]
        w = t.rewards[t.terminal]
        stats.mean('corr.terminal', ((v - v.mean())*(w - w.mean())).mean()/(v.var()*w.var())**.5)

        v = d.v[:-1][t.terminal[1:]]
        w = t.rewards[1:][t.terminal[1:]]
        stats.mean('corr.penultimate', ((v - v.mean())*(w - w.mean())).mean()/(v.var()*w.var())**.5)

def as_chunk(buffer, batch_size):
    chunk = arrdict.stack(buffer)
    terminal = torch.stack([chunk.transitions.terminal for _ in range(chunk.worlds.n_seats)], -1)
    chunk['reward_to_go'] = learning.reward_to_go(
        chunk.transitions.rewards.float(), 
        chunk.decisions.v.float(), 
        terminal).half()

    n_new = batch_size//terminal.size(1)
    chunk_stats(chunk, n_new)
            
    buffer = buffer[n_new:]

    return chunk, buffer

def rel_entropy(logits):
    valid = (logits > -np.inf)
    zeros = torch.zeros_like(logits)
    logits = logits.where(valid, zeros)
    probs = logits.exp().where(valid, zeros)
    return (-(logits*probs).sum(-1).mean(), torch.log(valid.sum(-1).float()).mean())

def optimize(network, scaler, opt, pbatch, vbatch):
    batch = arrdict.cat([pbatch, vbatch], 0)
    d0p = pbatch.decisions

    N = pbatch.transitions.terminal.size(0)
    with torch.cuda.amp.autocast():
        d = network(batch.worlds)
        dp, dv = d[:N], d[-N:]

        zeros = torch.zeros_like(dp.logits)
        l = dp.logits.where(dp.logits > -np.inf, zeros)
        l0 = d0p.logits.float().where(d0p.logits > -np.inf, zeros)

        policy_loss = -(l0.exp()*l).sum(axis=-1).mean()

        target_value = vbatch.reward_to_go
        value_loss = (target_value - dv.v).square().mean()

        loss = policy_loss + value_loss

    old = torch.cat([p.flatten() for p in network.parameters()])

    opt.zero_grad()
    scaler.scale(loss).backward()
    scaler.step(opt)
    scaler.update()

    new = torch.cat([p.flatten() for p in network.parameters()])

    with stats.defer():
        #TODO: Contract these all based on late-ness
        stats.mean('loss.value', value_loss)
        stats.mean('loss.policy', policy_loss)
        stats.mean('corr.resid-var', (target_value - dv.v).pow(2).mean(), target_value.pow(2).mean())

        p0 = d0p.prior.float().where(d0p.prior > -np.inf, zeros)
        stats.mean('kl-div.behaviour', (p0 - l0).mul(p0.exp()).sum(-1).mean())
        stats.mean('kl-div.prior', (p0 - l).mul(p0.exp()).sum(-1).mean())

        stats.mean('rel-entropy.policy', *rel_entropy(dp.logits)) 
        stats.mean('rel-entropy.targets', *rel_entropy(d0p.logits))

        stats.mean('v.target.mean', target_value.mean())
        stats.mean('v.target.std', target_value.std())
        stats.mean('v.target.max', target_value.abs().max())
        stats.mean('v.outputs.mean', dv.v.mean())
        stats.mean('v.outputs.std', dv.v.std())
        stats.mean('v.outputs.max', dv.v.abs().max())

        stats.mean('p.target.mean', l0.mean())
        stats.mean('p.target.std', l0.std())
        stats.mean('p.target.max', l0.abs().max())
        stats.mean('p.outputs.mean', l.mean())
        stats.mean('p.outputs.std', l.std())
        stats.mean('p.outputs.max', l.abs().max())

        stats.mean('policy-conc', l0.exp().max(-1).values.mean())

        stats.rate('sample-rate.learner', pbatch.transitions.terminal.nelement())
        stats.rate('step-rate.learner', 1)
        stats.cumsum('count.learner-steps', 1)
        # stats.rel_gradient_norm('rel-norm-grad', agent)

        stats.mean('opt.lr', np.mean([p['lr'] for p in opt.param_groups]))
        stats.mean('opt.step-std', (new - old).pow(2).mean().pow(.5))
        stats.max('opt.step-max', (new - old).abs().max())

def worldfunc(n_envs, device='cuda'):
    return hex.Hex.initial(n_envs=n_envs, boardsize=9, device=device)

def agentfunc(device='cuda'):
    worlds = worldfunc(n_envs=1, device=device)
    network = networks.FCModel(worlds.obs_space, worlds.action_space).to(worlds.device)
    return mcts.MCTSAgent(network, n_nodes=64)

def warm_start(agent, opt, parent):
    if parent:
        parent = runs.resolve(parent)
        sd = storage.load_latest(parent, device='cuda')
        agent.load_state_dict(sd['agent'])
        opt.load_state_dict(sd['opt'])
    return parent

def mix(worlds, T=2500):
    for _ in range(T):
        actions = torch.distributions.Categorical(probs=worlds.valid.float()).sample()
        worlds, transitions = worlds.step(actions)
    return worlds

@arrdict.mapping
def half(x):
    if isinstance(x, torch.Tensor) and x.dtype == torch.float:
        return x.half()
    else:
        return x

def run(pol_len=16, val_len=16, n_envs=8*1024, device='cuda', desc='default'):
    buffer_len = max(pol_len, val_len)

    #TODO: Restore league and sched when you go back to large boards
    worlds = mix(worldfunc(n_envs, device=device))
    agent = agentfunc(device)
    network = agent.network

    opt = torch.optim.Adam(network.parameters(), lr=1e-2, amsgrad=True)
    scaler = torch.cuda.amp.GradScaler()

    parent = warm_start(agent, opt, '')

    run = runs.new_run(boardsize=worlds.boardsize, parent=parent, description=desc)

    archive.archive(run)

    buffer = []
    with logs.to_run(run), stats.to_run(run), \
            arena.monitor(run, worldfunc, agentfunc, device=worlds.device):
        #TODO: Upgrade this to handle batches that are some multiple of the env count
        pol_idxs = (torch.randint(buffer_len - pol_len, buffer_len, (n_envs,), device=device), torch.arange(n_envs, device=device))
        val_idxs = (torch.randint(buffer_len - val_len, buffer_len, (n_envs,), device=device), torch.arange(n_envs, device=device))
        while True:

            # Collect experience
            while len(buffer) < buffer_len:
                with torch.no_grad():
                    decisions = agent(worlds, value=True)
                new_worlds, transition = worlds.step(decisions.actions)

                buffer.append(arrdict.arrdict(
                    worlds=worlds,
                    decisions=decisions.half(),
                    transitions=half(transition)).detach())

                worlds = new_worlds

                log.info(f'({len(buffer)}/{buffer_len}) actor stepped')

            # Optimize
            chunk, buffer = as_chunk(buffer, n_envs)
            optimize(network, scaler, opt, chunk[pol_idxs], chunk[val_idxs])
            
            log.info('learner stepped')

            sd = storage.state_dicts(agent=agent, opt=opt)
            storage.throttled_latest(run, sd, 60)
            storage.throttled_snapshot(run, sd, 900)
            storage.throttled_raw(run, 'model', lambda: pickle.dumps(network), 900)
            stats.gpu(worlds.device, 15)

def run_experiment():
    #TODO: This is a garbage fire.
    import os
    import shlex
    from subprocess import Popen, PIPE
    from signal import SIGINT
    lens = [1, 4, 16, 64, 256][::-1]
    queue = []
    for pol_len in [256]:
        for val_len in [4, 16, 64, 256]:
            queue.append({'pol_len': pol_len, 'val_len': val_len})
    
    starts = {i: (0, None) for i in (0, 1)}
    while True:
        for i, (start, old) in starts.items():
            if time.time() > start + 3600:
                if old is not None:
                    log.info(f'Interrupting {i}')
                    old.send_signal(SIGINT)
                    old.wait(15)

                params = queue.pop()
                log.info(f'Launching {params} on {i}')
                env = os.environ.copy()
                env['CUDA_VISIBLE_DEVICES'] = str(i)
                desc = f'experiments/buffer-len/pol-{params["pol_len"]}/val-{params["val_len"]}'
                cmd = f'''python -c "from boardlaw.main import *; run(pol_len={params["pol_len"]}, val_len={params["val_len"]}, desc='{desc}')"'''
                new = Popen(
                    shlex.split(cmd), 
                    env=env,
                    stdout=PIPE,
                    stderr=PIPE)
                starts[i] = (time.time(), new)
            else:
                if old is not None:
                    if old.poll():
                        print(f'Crashed {i}, retcode {old.returncode}')
                        import aljpy; aljpy.extract()
            
            time.sleep(5)

def show_experiment():
    import pandas as pd
    import re
    rs = runs.pandas().loc[lambda df: df.description.fillna('').str.startswith('experiments/buffer-len')]
    df = {}
    for r, row in rs.iterrows():
        m = re.match(r'.*/.*/pol-(\d+)/val-(\d+)', row.description)
        df[int(m.group(1)), int(m.group(2))] = stats.pandas(r, 'elo-mohex')['μ']
    df = pd.concat(df, 1).ffill().iloc[-1].unstack()
    df.index.name = 'policy buffer'
    df.columns.name = 'value buffer'

    ax = df.T.plot(logx=True, marker='o', linestyle='--', grid=True)
    ax.set_title('experiment/buffer-len')
            

@profiling.profilable
def benchmark_experience_collection(n_envs=8192, T=4):
    import pandas as pd

    if n_envs is None:
        ns = np.logspace(0, 15, 16, base=2, dtype=int)
        return pd.Series({n: benchmark_experience_collection(n) for n in ns})

    torch.manual_seed(0)
    worlds = worldfunc(n_envs)
    agent = agentfunc()

    agent(worlds) # warmup

    torch.cuda.synchronize()
    start = time.time()
    for _ in range(T):
        decisions = agent(worlds)
        new_worlds, transition = worlds.step(decisions.actions)
        worlds = new_worlds
        print('actor stepped')
    torch.cuda.synchronize()
    rate = (T*n_envs)/(time.time() - start)
    print(f'{n_envs}: {rate}/sample')

    return rate

if __name__ == '__main__':
    with torch.autograd.profiler.emit_nvtx():
        benchmark_experience_collection()
