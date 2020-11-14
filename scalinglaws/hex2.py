import torch
from . import heads
from rebar import arrdict
import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np

# Empty, 
# black, black win, black-north-connected, black-south-connected
# white, white win, white-west-connected, white-east-connected
_STRINGS = '.bB^vwW<>'

def _cell_states(device):
    return {s: torch.tensor(i, dtype=torch.int, device=device) for i, s in enumerate(_STRINGS)}

class BoardHelper:

    def __init__(self, board):
        self.board = board.clone()
        self.n_envs, self.boardsize = board.shape[:2]
        self.device = self.board.device
        self.envs = torch.arange(self.n_envs, device=self.device)

        self._STATES = _cell_states(self.device)

        self._IS_EDGE = {
            '^': lambda idxs: idxs[..., 0] == 0,
            'v': lambda idxs: idxs[..., 0] == self.boardsize-1,
            '<': lambda idxs: idxs[..., 1] == 0,
            '>': lambda idxs: idxs[..., 1] == self.boardsize-1}

        self._NEIGHBOURS = torch.tensor([(-1, 0), (-1, +1), (0, -1), (0, +1), (+1, -1), (+1, +0)], device=self.device, dtype=torch.long)

    def cells(self, idxs, val=None):
        if idxs.size(-1) == 2:
            rows, cols = idxs[..., 0], idxs[..., 1]
            envs = self.envs[(slice(None),) + (None,)*(idxs.ndim-2)].expand_as(rows)
        else: # idxs.size(-1) == 3
            envs, rows, cols = idxs[..., 0], idxs[..., 1], idxs[..., 2]
        
        if val is None:
            return self.board[envs, rows, cols]
        else:
            self.board[envs, rows, cols] = val
    
    def neighbours(self, idxs):
        if idxs.size(1) == 3:
            neighbours = self.neighbours(idxs[:, 1:])
            envs = idxs[:, None, [0]].expand(-1, len(self._NEIGHBOURS), 1)
            return torch.cat([envs, neighbours], 2)
        return (idxs[:, None, :] + self._NEIGHBOURS).clamp(0, self.boardsize-1)

    def colours(self, x):
        colours = x.clone()
        colours[(x == self._STATES['^']) | (x == self._STATES['v'])] = self._STATES['b']
        colours[(x == self._STATES['<']) | (x == self._STATES['>'])] = self._STATES['w']
        return colours

    def flood(self, actions):
        # This eats 70% of the game's runtime.
        moves = self.cells(actions)
        colors = self.colours(moves)

        active = torch.stack([moves == self._STATES[s] for s in '<>^v'], 0).any(0)

        idxs = torch.cat([self.envs[:, None], actions], 1)[active]
        while idxs.size(0) > 0:
            self.cells(idxs, moves[idxs[:, 0]])
            neighbour_idxs = self.neighbours(idxs)
            possible = self.cells(neighbour_idxs) == colors[idxs[:, 0], None]

            touched = torch.zeros_like(self.board, dtype=torch.bool)
            touched[tuple(neighbour_idxs[possible].T)] = True
            idxs = touched.nonzero()

    def reset(self, terminate):
        self.board[terminate] = self._STATES['.']

    def step(self, seat, actions):
        assert (self.cells(actions) == 0).all(), 'One of the actions is to place a token on an already-occupied cell'

        neighbours = self.cells(self.neighbours(actions))

        black = seat == 0
        white = seat == 1
        conns = {s: ((neighbours == self._STATES[s]).any(-1)) | self._IS_EDGE[s](actions) for s in self._IS_EDGE}

        new_cells = torch.zeros_like(self.cells(actions))
        
        new_cells[black] = self._STATES['b']
        new_cells[black & conns['^']] = self._STATES['^']
        new_cells[black & conns['v']] = self._STATES['v']
        new_cells[black & conns['^'] & conns['v']] = self._STATES['B']

        new_cells[white] = self._STATES['w']
        new_cells[white & conns['<']] = self._STATES['<']
        new_cells[white & conns['>']] = self._STATES['>']
        new_cells[white & conns['<'] & conns['>']] = self._STATES['W']

        terminal = ((new_cells == self._STATES['B']) | (new_cells == self._STATES['W']))

        self.cells(actions, new_cells)
        self.flood(actions)
        self.reset(terminal)

        return terminal

HexStateBase = arrdict.namedarrtuple('HexStateBase', ('board', 'seat'))
class HexState(HexStateBase):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.n_seats = 2
        self.n_envs = self.board.shape[0]
        self.boardsize = self.board.shape[1]
        self.device = self.board.device

        self.obs_space = heads.Tensor((self.boardsize, self.boardsize, 2))
        self.action_space = heads.Masked(self.boardsize*self.boardsize)

    def _next_state(self, actions):
        if actions.ndim == 1:
            actions = torch.stack([actions // self.boardsize, actions % self.boardsize], -1)

        helper = BoardHelper(self.board)

        # White player sees a transposed board, so their actions need transposing back.
        black_actions = actions
        white_actions = actions.flip(1)
        actions = torch.where(self.seat[:, None] == 0, black_actions, white_actions)

        terminal = helper.step(self.seat, actions)

        new_seat = 1 - self.seat
        new_seat[terminal] = 0

        new_state = type(self)(board=helper.board, seat=new_seat)

        return terminal, new_state

    def observe(self):
        cell_states = _cell_states(self.device)
        black_view = torch.stack([
            torch.stack([self.board == cell_states[s] for s in 'b^vB']).any(0),
            torch.stack([self.board == cell_states[s] for s in 'w<>W']).any(0)], -1).float()

        # White player sees a transposed board
        white_view = black_view.transpose(1, 2).flip(3)
        obs = black_view.where(self.seat[:, None, None, None] == 0, white_view)

        return arrdict.arrdict(
            obs=obs,
            valid=(obs == 0).all(-1).reshape(self.n_envs, -1),
            seats=self.seat).clone()

    def step(self, actions):
        """Args:
            actions: (n_env, 2)-int tensor between (0, 0) and (boardsize, boardsize). Cells are indexed in row-major
            order from the top-left.
            
        Returns:

        """
        terminal, new_state = self._next_state(actions)

        rewards = torch.zeros((self.n_envs, self.n_seats), device=self.device)
        rewards.scatter_(1, self.seat[:, None].long(), terminal[:, None].float())
        rewards.scatter_(1, 1-self.seat[:, None].long(), -terminal[:, None].float())

        transition = arrdict.arrdict(
            terminal=terminal, 
            rewards=rewards)
        return transition, new_state

    @classmethod
    def plot_state(cls, state, e=0, ax=None):
        board = state[e].board
        width = board.shape[1]

        ax = plt.subplots()[1] if ax is None else ax
        ax.set_aspect(1)

        sin60 = np.sin(np.pi/3)
        ax.set_xlim(-1.5, 1.5*width)
        ax.set_ylim(-sin60, sin60*width)

        rows, cols = np.indices(board.shape)
        coords = np.stack([
            cols + .5*np.arange(board.shape[0])[:, None],
            # Hex centers are 1 apart, so distances between rows are sin(60)
            sin60*(board.shape[0] - 1 - rows)], -1).reshape(-1, 2)

        black = 'dimgray'
        white = 'lightgray'
        colors = ['tan'] + [black]*4 + [white]*4
        colors = np.vectorize(colors.__getitem__)(board).flatten()


        tl, tr = (-1.5, (width)*sin60), (width-.5, (width)*sin60)
        bl, br = (width/2-1, -sin60), (1.5*width, -sin60)
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
                        facecolors=colors, 
                        edgecolor='k', 
                        linewidths=1, 
                        transOffset=ax.transData,
                        zorder=2)

        ax.add_collection(hexes)
        ax.set_frame_on(False)
        ax.set_xticks([])
        ax.set_yticks([])

        return ax.figure

    def display(self, e=0):
        ax = self.plot_state(arrdict.numpyify(self), e=e)
        plt.close(ax.figure)
        return ax

def create(n_envs, boardsize=11, device='cuda'):
    # As per OpenSpiel and convention, black plays first.
    return HexState(
        board=torch.full((n_envs, boardsize, boardsize), 0, device=device, dtype=torch.int),
        seat=torch.full((n_envs,), 0, device=device, dtype=torch.int))

## TESTS ##

def board_size(s):
    return len(s.strip().splitlines())

def board_actions(s):
    size = board_size(s)
    board = (np.frombuffer((s.strip() + '\n').encode(), dtype='S1')
                 .reshape(size, size+1)
                 [:, :-1])
    indices = np.indices(board.shape)

    bs = indices[:, board == b'b'].T
    ws = indices[:, board == b'w'].T

    assert len(bs) - len(ws) in {0, 1}

    actions = []
    for i in range(len(ws)):
        actions.append([bs[i, 0], bs[i, 1]])
        actions.append([ws[i, 1], ws[i, 0]])

    if len(ws) < len(bs):
        actions.append([bs[-1, 0], bs[-1, 1]])

    return torch.tensor(actions)

def from_string(s, **kwargs):
    """Example:
    
    s = '''
    bwb
    wbw
    ...
    '''
    
    """
    state = create(n_envs=1, boardsize=board_size(s), **kwargs)
    for a in board_actions(s):
        response, state = state.step(a[None])
    return state

def test_basic():
    s = create(1, 3, device='cpu')

    for _ in range(20):
        o = s.observe()
        actions = torch.distributions.Categorical(probs=o.valid.float()).sample()
        t, s = s.step(actions)

def open_spiel_board(state):
    # state ordering taken from hex.h 
    strs = 'W<>w.bv^B'
    board = np.array(state.observation_tensor()).reshape(9, 11, 11).argmax(0)
    strs = np.vectorize(strs.__getitem__)(board)
    return '\n'.join(' '*i + ' '.join(r) for i, r in enumerate(strs))

def open_spiel_display_str(env, e):
    strs = _STRINGS
    board = env.board[e].clone()
    strings = np.vectorize(strs.__getitem__)(board.cpu().numpy())
    return '\n'.join(' '*i + ' '.join(r) for i, r in enumerate(strings))

def test_open_spiel():
    import pyspiel

    e = 1
    ours = create(3, 11, device='cpu')

    theirs = pyspiel.load_game("hex")
    state = theirs.new_initial_state()
    while True:
        new = ours.observe()
        seat = new.seats[e]
        our_action = torch.distributions.Categorical(probs=new.valid.float()).sample()
        t, ours = ours.step(our_action)

        if seat == 0:
            their_action = our_action[e]
        else: #if new.player == 1:
            r, c = our_action[e]//ours.boardsize, our_action[e] % ours.boardsize
            their_action = c*ours.boardsize + r

        state.apply_action(their_action)
            
        if t.terminal[e]:
            assert state.is_terminal()
            break
            
        our_state = open_spiel_display_str(ours, e)
        their_state = open_spiel_board(state)
        assert our_state == their_state

def benchmark(n_envs=4096, n_steps=256):
    import aljpy
    state = create(n_envs)

    torch.cuda.synchronize()
    with aljpy.timer() as timer:
        for _ in range(n_steps):
            obs = state.observe()
            actions = torch.distributions.Categorical(probs=obs.valid.float()).sample()
            _, state = state.step(actions)
        
        torch.cuda.synchronize()
    print(f'{n_envs*n_steps/timer.time():.0f} samples/sec')

def test_subenvs():
    state = create(n_envs=3, boardsize=5, device='cpu')
    substate = state[[1]]
    _, substate = substate.step(torch.tensor([[0, 0]], dtype=torch.long))
    state[[1]] = substate

    board = state.board
    assert (board[[0, 2]] == 0).all()
    assert (board[1][1:, :] == 0).all()
    assert (board[1][:, 1:] == 0).all()
    assert (board[1][0, 0] != 0).all()