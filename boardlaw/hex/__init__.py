from rebar import arrdict, profiling
import numpy as np
import matplotlib.pyplot as plt
import matplotlib as mpl
import torch
from .. import heads
from . import cuda

CHARS = '.bwTBLR'
ORDS = {c: i for i, c in enumerate(CHARS)}

def color_board(board, colors='obs'):
    black = (0, 0, .4)
    white = (0, 0, .8)
    tan = (.07, .4, .8)
    if colors == 'obs':
        colors = [tan, black, white, black, black, white, white] 
    elif colors == 'board':
        colors = [tan, black, white, (.16, .2, .4), (.33, .2, .4), (.66, .2, .8), (.72, .2, .8)]
    colors = np.stack([mpl.colors.hsv_to_rgb(c) for c in colors])
    colors = colors[board]
    return colors

def color_obs(obs):
    keyed = np.zeros_like(obs[..., 0], dtype=int)
    keyed[obs[..., 0] == 1.] = 1
    keyed[obs[..., 1] == 1.] = 2
    return color_board(keyed)

def plot_board(colors, ax=None, black='dimgray', white='lightgray', edges=True):
    ax = plt.subplots()[1] if ax is None else ax
    ax.set_aspect(1)

    width = colors.shape[0]

    sin60 = np.sin(np.pi/3)
    ax.set_xlim(-1.5, 1.5*width)
    ax.set_ylim(-sin60, sin60*width)

    size = width*width
    rows, cols = np.indices((width, width))
    coords = np.stack([
        cols + .5*np.arange(width)[:, None],
        # Hex centers are 1 apart, so distances between rows are sin(60)
        sin60*(width - 1 - rows)], -1).reshape(-1, 2)

    tl, tr = (-1.5, (width)*sin60), (width-.5, (width)*sin60)
    bl, br = (width/2-1, -sin60), (1.5*width, -sin60)
    if edges:
        ax.add_patch(mpl.patches.Polygon(np.array([tl, tr, bl, br]), linewidth=1, edgecolor='k', facecolor=black, zorder=1))
        ax.add_patch(mpl.patches.Polygon(np.array([tl, bl, tr, br]), linewidth=1, edgecolor='k', facecolor=white, zorder=1))

    radius = .5/sin60
    data_to_pixels = ax.transData.get_matrix()[0, 0]
    pixels_to_points = 1/ax.figure.get_dpi()*72.
    size = np.pi*(data_to_pixels*pixels_to_points*radius)**2
    sizes = (size,)*len(coords)

    hexes = mpl.collections.RegularPolyCollection(
                    numsides=6, 
                    sizes=sizes,
                    offsets=coords, 
                    facecolors=colors.reshape(-1, colors.shape[-1]), 
                    edgecolor='k', 
                    linewidths=1, 
                    transOffset=ax.transData,
                    zorder=2)
    ax.add_collection(hexes)

    ax.set_frame_on(False)
    ax.set_xticks([])
    ax.set_yticks([])

    return ax

class Hex(arrdict.namedarrtuple(fields=('board', 'seats'))):

    @classmethod
    def initial(cls, n_envs, boardsize=11, device='cuda'):
        # As per OpenSpiel and convention, black plays first.
        return cls(
            board=torch.full((n_envs, boardsize, boardsize), 0, device=device, dtype=torch.uint8),
            seats=torch.full((n_envs,), 0, device=device, dtype=torch.int))

    @profiling.nvtx
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not isinstance(self.board, torch.Tensor):
            # Need this conditional to deal with the case where we're calling a method like `self.clone()`, and the
            # intermediate arrdict generated is full of methods, which will break this here init function.
            return 

        self.n_seats = 2
        self.n_envs = self.board.shape[0]
        self.boardsize = self.board.shape[1]
        self.device = self.board.device

        self.obs_space = heads.Tensor((self.boardsize, self.boardsize, 2))
        self.action_space = heads.Masked(self.boardsize*self.boardsize)

        self._obs = None
        self._valid = None 

    @property
    def obs(self):
        if self._obs is None:
            self._obs = cuda.observe(self.board, self.seats)
        return self._obs

    @property
    def valid(self):
        if self._valid is None:
            shape = self.board.shape[:-2]
            self._valid = (self.obs == 0).all(-1).reshape(*shape, -1)
        return self._valid

    @profiling.nvtx
    def step(self, actions):
        """Args:
            actions: (n_env, 2)-int tensor between (0, 0) and (boardsize, boardsize). Cells are indexed in row-major
            order from the top-left.
            
        Returns:

        """
        if self.board.ndim != 3:
            #TODO: Support stepping arbitrary batchings. Only needs a reshaping.
            raise ValueError('You can only step a board with a single batch dimension')

        assert (0 <= actions).all(), 'You passed a negative action'
        if actions.ndim == 2:
            actions = actions[..., 0]*self.boardsize + actions[:, 1]

        assert actions.shape == (self.n_envs,)
        assert self.valid.gather(1, actions[:, None]).squeeze(-1).all()

        new_board = self.board.clone()
        rewards = cuda.step(new_board, self.seats.int(), actions.int())
        terminal = (rewards > 0).any(-1)

        new_board[terminal] = 0

        new_seat = 1 - self.seats
        new_seat[terminal] = 0

        new_world = type(self)(board=new_board, seats=new_seat)

        transition = arrdict.arrdict(
            terminal=terminal, 
            rewards=rewards)
        return new_world, transition

    @profiling.nvtx
    def __getitem__(self, x):
        # Just exists for profiling
        return super().__getitem__(x)

    @profiling.nvtx
    def __setitem__(self, x, y):
        # Just exists for profiling
        return super().__setitem__(x, y)

    @classmethod
    def plot_worlds(cls, worlds, e=None, ax=None, colors='obs'):
        e = (0,)*(worlds.board.ndim-2) if e is None else e
        board = worlds.board[e]

        ax = plt.subplots()[1] if ax is None else ax

        colors = color_board(board, colors)
        plot_board(colors, ax)

        return ax.figure

    def display(self, e=None, **kwargs):
        ax = self.plot_worlds(arrdict.numpyify(arrdict.arrdict(self)), e=e, **kwargs)
        plt.close(ax.figure)
        return ax

class Solitaire(Hex):
    """One-player Hex"""

    @classmethod
    def initial(cls, *args, seat=0, **kwargs):
        worlds = super().initial(*args, **kwargs)
        if seat == 1:
            raise ValueError('Can\'t do seat #1 right now')
        return worlds

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.n_seats = 1

    def step(self, actions):
        worlds, transitions = super().step(actions)

        # Might be that the move just made wins the game, in which case we need to 
        # step the world until we get to the same seat again.
        while True:
            mask = (worlds.seats != self.seats)
            if not mask.any():
                break
            worlds[mask], other = self._play(worlds[mask])
            transitions.rewards[mask] += other.rewards
            transitions.terminal[mask] |= other.terminal

        envs = torch.arange(self.n_envs, device=self.device)
        transitions['rewards'] = transitions.rewards[envs, self.seats.long()][:, None]
        return worlds, transitions

class Lazy(Solitaire):
    """Opponent plays the first available action"""

    @classmethod
    def _play(cls, worlds):
        n_actions = worlds.valid.size(1)
        actions = torch.arange(n_actions, device=worlds.device)[None, :].expand_as(worlds.valid).clone()
        actions[~worlds.valid] = n_actions
        return Hex.step(worlds, actions.min(-1).values)

class Random(Solitaire):
    """Opponent plays a random action"""

    @classmethod
    def _play(cls, worlds):
        actions = torch.distributions.Categorical(probs=worlds.valid.float()).sample()
        return Hex.step(worlds, actions)


def test_bug():
    worlds = Hex.initial(n_envs=1, boardsize=3)
    actions = torch.tensor([5, 5, 6, 1], device=worlds.device)
    for a in actions:
        worlds, transitions = worlds.step(a[None])
    torch.testing.assert_allclose(worlds.board[0], torch.tensor([
        [0, 0, 0],
        [5, 0, 1],
        [4, 2, 0]], device=worlds.device))

def test_bug_2():
    worlds = Hex.initial(n_envs=1, boardsize=3)
    worlds.board[:] = torch.tensor([
        [0, 6, 6],
        [1, 1, 1],
        [0, 2, 0]], device=worlds.device, dtype=torch.uint8)
    worlds.seats[:] = 0

    worlds, transitions = worlds.step(torch.tensor([6], device=worlds.device))

    torch.testing.assert_allclose(worlds.board[0], torch.tensor([
        [0, 6, 6],
        [4, 4, 4],
        [4, 2, 0]], device=worlds.device))