import asyncio
from collections import defaultdict

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

CATEGORY_COLORS = {
    "emergency": "red",
    "disabled": "tab:blue",
    "shipping": "tab:orange",
    "big_family": "tab:green",
    "rest": "gray",
}

MAP_W, MAP_H = 80, 70


def _short(jid):
    return str(jid).split("@")[0]


def _curb_xy(car, curb_jid):
    for c in car.curbs:
        if c.get("jid") == curb_jid:
            return float(c["x"]), float(c["y"])
    return None


def _draw(ax, curbs, cars):
    ax.clear()
    ax.set_xlim(0, MAP_W)
    ax.set_ylim(0, MAP_H)
    ax.set_aspect("equal")
    ax.grid(alpha=0.3)
    ax.set_title("Curbside parking")

    # Green curb - free, yellow - partially occupied, red - full
    for curb in curbs:
        logic = getattr(curb, "logic", None)
        if logic is None:
            continue
        x, y = logic.location
        used, cap = logic.occupied_slots, logic.capacity_spaces
        ratio = min(used / cap, 1.0) if cap else 1.0
        ax.scatter(x, y, s=1800, marker="s", color=(ratio, 0.8 - 0.5 * ratio, 0.2),
                   alpha=0.35, edgecolors="black", zorder=1)
        ev = " (EV)" if logic.has_ev_spots else ""
        ax.annotate(f"{_short(curb.jid)}{ev}\n{used}/{cap}", (x, y - 6),
                    ha="center", fontsize=8)

    # Cars on the same curb are drawn with offset, not to overlap
    slot_idx = defaultdict(int)
    for car in cars:
        state = getattr(car, "state", "seeking")
        if state == "leaving":
            continue
        x, y = car.x, car.y
        if state == "parked" and car.target_curb:
            i = slot_idx[car.target_curb]
            slot_idx[car.target_curb] += 1
            x += 3 * i
            y += 3
        elif state == "commuting" and car.target_curb:
            target = _curb_xy(car, car.target_curb)
            if target:
                ax.plot([car.x, target[0]], [car.y, target[1]], "--",
                        color="black", alpha=0.3, zorder=2)

        ax.scatter(x, y, s=120 + 100 * (car.size_units - 1),
                   color=CATEGORY_COLORS.get(car.category, "gray"),
                   edgecolors="red" if car.debt > 0 else "black",
                   linewidths=2.5 if car.debt > 0 else 1, zorder=3)
        label = f"{_short(car.jid)}\n{state}"
        if car.debt > 0:
            label += f"\nfine ${car.debt:.0f}"
        ax.annotate(label, (x, y + 2.5), ha="center", fontsize=7, zorder=4)

    legend = [Line2D([0], [0], marker="o", color="w", markerfacecolor=c,
                     markeredgecolor="black", markersize=9, label=cat)
              for cat, c in CATEGORY_COLORS.items()]
    ax.legend(handles=legend, loc="upper right", fontsize=7)


async def viz_loop(curbs, cars, fps=10):
    plt.ion()
    fig, ax = plt.subplots(figsize=(8, 7))
    plt.show(block=False)
    try:
        while plt.fignum_exists(fig.number):
            _draw(ax, curbs, cars)
            fig.canvas.draw_idle()
            fig.canvas.flush_events()
            await asyncio.sleep(1 / fps)
    except asyncio.CancelledError:
        pass
    finally:
        plt.close(fig)
