import pandas as pd
from plotnine import *
from . import data

def mpl_theme(width=12, height=8):
    return [
        theme_matplotlib(),
        guides(
            color=guide_colorbar(ticks=False)),
        theme(
            figure_size=(width, height), 
            strip_background=element_rect(color='w', fill='w'),
            panel_grid=element_line(color='k', alpha=.1))]

def plot_sigmoids(aug):
    return (ggplot(data=aug, mapping=aes(x='width', y='rel_elo', color='depth'))
        + geom_line(mapping=aes(group='depth'))
        + geom_point()
        + facet_wrap('boardsize', nrow=1)
        + scale_x_continuous(trans='log2')
        + scale_color_continuous(trans='log2')
        + coord_cartesian((-.1, None), (0, 1), expand=False)
        + labs(
            title='larger boards lead to slower scaling',
            y='normalised elo (entirely random through to perfect play)')
        + mpl_theme(18, 6))

def plot_sample_eff():
    df = data.load()

    return (ggplot(
        data=df
            .iloc[5:]
            .rolling(15, 1).mean()
            .unstack().unstack(0)
            .loc[7]
            .reset_index()
            .dropna()) + 
        geom_line(aes(x='np.log10(samples)', y='elo/9.03 + 1', group='depth', color='np.log2(depth)')) + 
        labs(title='sample efficiency forms a large part of the advantage of depth (7x7)') + 
        facet_wrap('width') +
        mpl_theme(18, 12))

def plot_convergence_rate(df):
    df = data.load()

    diffs = {}
    for b, t in data.TAILS.items():
        live = df.elo[b].dropna(0, 'all')
        diffs[b] = (live - live.iloc[-1])/data.min_elos().abs()[b]
    diffs = pd.concat(diffs, 1)

    (ggplot(
        data=(
            diffs
                .unstack()
                .rename('elo')
                .reset_index()
                .rename(columns={'level_0': 'boardsize'})
                .dropna()
                .assign(
                    s=lambda df: df._time.astype(int)/1e9,
                    g=lambda df: df.depth.astype(str) + df.width.astype(str)))) + 
        geom_line(aes(x='_time', y='elo', group='g', color='np.log2(width)')) + 
        facet_wrap('boardsize', scales='free') +
        plots.mpl_theme() + 
        labs(title='runs converge much faster than I thought') + 
        theme(panel_spacing_y=.3, panel_spacing_x=.5))

def flops(df):
    intake = (df.boardsize**2 + 1)*df.width
    body = (df.width**2 + df.width) * df.depth
    output = df.boardsize**2 * (df.width + 1)
    return 64*df.samples*(intake + body + output)

def params(df):
    intake = (df.boardsize**2 + 1)*df.width
    body = (df.width**2 + df.width) * df.depth
    output = df.boardsize**2 * (df.width + 1)
    return intake + body + output

def plot_compute_frontier():
    df = data.load()
    (ggplot(
            data=df
                .iloc[5:]
                .pipe(lambda df: df.ewm(span=10).mean().where(df.bfill().notnull()))
                .unstack().unstack(0)
                .reset_index()
                .assign(params=params)
                .assign(flops=flops)
                .assign(g=lambda df: df.width.astype(str)+df.depth.astype(str))
                .assign(norm_elo=data.normalised_elo)
                .dropna()) + 
            geom_line(aes(x='flops', y='norm_elo', color='params', group='g')) + 
            #labs(title='compute-efficient frontier is dominated by the low-depth architectures') +
            scale_x_continuous(trans='log10') + 
            scale_color_continuous(trans='log10') + 
            facet_wrap('boardsize') +
            coord_cartesian(None, (0, 1)) +
            mpl_theme(18, 15))
