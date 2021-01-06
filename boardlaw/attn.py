"""
Idea:
    * Attend to the board
    * Say main layers are 256 neurons
    * Then at each layer, compress the input down to 8
    * At each location, stack it with the position (4?) and 3x3 board features (9 or 18, depending)
    * That gives 8 + 4 + 18 = 30 input, which you can stack with the 8 and transform into 16?
    * On a 9x9 board, that works out to about the cost of one 256x256 layer
    * Attend to it with a 16 key, return a 16 value, expand that value up to 256 again and add it in
    * Then ReZero it into the trunk.

"""
import numpy as np
import torch
from torch import nn
from torch.nn import functional as F

OFFSETS = [(-1, 0), (-1, 1), (0, -1), (0, 0), (0, +1), (-1, -1), (-1, 0)]

def plot(p):
    import matplotlib.pyplot as plt
    from .hex import plot_board
    plot_board(np.stack(np.vectorize(plt.cm.RdBu)(.5+.5*p), -1))


def positions(boardsize):
    # https://www.redblobgames.com/grids/hexagons/#conversions-axial
    #TODO: Does it help to sin/cos encode this?
    rs, cs = torch.meshgrid(
            torch.linspace(-1, 1, boardsize),
            torch.linspace(-1, 1, boardsize))
    zs = (rs + cs)/2.
    return torch.stack([rs, cs, zs], -1)

def offset(board, o):
    w = board.shape[-1]
    r, c = o
    t, b = 1+r, w-1+r
    l, r = 1+c, w-1+c
    return board[..., t:b, l:r]

def neighbourhoods(obs):
    single = obs[..., 0] - obs[..., 1]
    augmented = F.pad(single, (1, 1, 1, 1))
    return torch.stack([offset(augmented, o) for o in OFFSETS], -1)

def prepare(obs, pos):
    stack = torch.cat([neighbourhoods(obs), pos[None]], -1)
    B, H, W, C = stack.shape
    return stack.reshape(B, H*W, C)

class Attention:

    def __init__(self, d_trunk, d_key=16):
        d_obs = 7
        d_pos = 3
        self.prekey = nn.Linear(d_trunk, d_key)
        self.kv = nn.Linear(d_obs+d_pos+d_trunk, 2*d_key)
        self.q = nn.Linear(d_trunk, d_key)

    def forward(self, x, prep):

        pk = torch.cat([self.prekey(x), prep], -1)
        k, v = self.kv(pk).chunk(2, -1)
        q = self.q(x)
        torch.einsum('brc')

def test():
    from boardlaw.hex import Hex

    worlds = Hex.initial(1, 5)

    pos = positions(worlds.boardsize).to(worlds.device)
    prep = prepare(worlds.obs, pos)