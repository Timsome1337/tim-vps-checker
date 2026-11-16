"""Dark-mode PNG charts for Tim VPS Checker; plotting is optional at import time."""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from io import BytesIO
from math import nan
from zoneinfo import ZoneInfo

SAMARA = ZoneInfo('Europe/Samara')
TITLES = {'24h': '24 часа', '7d': '7 дней', '30d': '30 дней'}


def draw_chart(rows: list[dict], period: str, server_name: str) -> bytes:
    if not rows:
        raise ValueError('Empty history')
    # Import lazily: this lets the rest of the monitor run even if charting is unavailable.
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    import numpy as np

    times = [datetime.fromtimestamp(r['ts'], timezone.utc).astimezone(SAMARA) for r in rows]
    metrics = [('cpu', 'CPU · Процессор', '#6daafa'),
               ('memory', 'RAM · Память', '#72d0ae'),
               ('disk', 'Disk · Занято', '#edb772'),
               ('free_gb', 'Disk · Свободно, ГБ', '#a6c48a')]
    bg, panel, fg = '#101a2b', '#1e2b40', '#e4ecf5'
    fig, axes = plt.subplots(4, 1, figsize=(9.4, 11.1), sharex=True, dpi=130)
    fig.patch.set_facecolor(bg)
    for ax, (field, label, color) in zip(axes, metrics):
        ax.set_facecolor(panel)
        if field == 'free_gb':
            vals = np.array([(max(0, float(r['disk_total']) - float(r['disk_used'])) / 1e9)
                             if r.get('ok') and r.get('disk_total') is not None and
                             r.get('disk_used') is not None else nan for r in rows])
        else:
            vals = np.array([float(r[field]) if r.get('ok') and r.get(field) is not None else nan for r in rows])
        ax.plot(times, vals, color=color, linewidth=2.2, marker='o' if len(rows) < 5 else None,
                markersize=4)
        ax.fill_between(times, vals, color=color, alpha=0.14)
        ax.set_ylabel('ГБ' if field == 'free_gb' else '%', color=fg, rotation=0, labelpad=15)
        available = vals[np.isfinite(vals)]
        if field == 'cpu':
            peak = float(max(available)) if len(available) else 0
            cap = next((n for n in (5, 10, 20, 30, 50, 75, 100)
                        if peak <= n * 0.86), 100)
            ax.set_ylim(0, cap)
            ax.set_yticks(np.linspace(0, cap, 5))
        elif field == 'free_gb':
            peak = float(max(available)) if len(available) else 1.0
            ax.set_ylim(0, max(1.0, peak * 1.2))
            ax.yaxis.set_major_locator(plt.MaxNLocator(4))
        else:
            ax.set_ylim(0, 100)
            ax.set_yticks([0, 25, 50, 75, 100])
        ax.set_title(label, loc='left', fontsize=12, color=fg, pad=10, fontweight='bold')
        ax.grid(axis='y', alpha=0.16, color='#d3e1ee')
        ax.tick_params(axis='both', colors='#adbdcf', labelsize=10)
        for spine in ax.spines.values(): spine.set_visible(False)
    if len(times) == 1 or (max(times) - min(times)).total_seconds() < 60:
        # Avoid repeating 00:00 labels when history has only one point.
        for ax in axes:
            ax.set_xlim(times[0] - timedelta(minutes=30), times[0] + timedelta(minutes=30))
    axes[-1].xaxis.set_major_locator(mdates.AutoDateLocator(minticks=3, maxticks=6, tz=SAMARA))
    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter('%d.%m %H:%M' if period != '24h' else '%H:%M', tz=SAMARA))
    for lbl in axes[-1].get_xticklabels(): lbl.set_rotation(0)
    fig.suptitle(f'Tim VPS Checker  ·  {TITLES[period]}', color=fg, fontsize=17,
                 fontweight='bold', y=.985)
    fig.text(.07, .925, 'Время: Самара (UTC+4)   •   Значения с момента включения истории',
             color='#9cb1c7', fontsize=10)
    fig.subplots_adjust(left=.12, right=.975, bottom=.08, top=.86, hspace=.62)
    output = BytesIO()
    try:
        fig.savefig(output, format='png', facecolor=bg, dpi=130)
        return output.getvalue()
    finally:
        plt.close(fig)
