"""
Pygame street view for the curbside parking demo.

Reads live positions from world_state (updated by SPADE agents).
"""

from __future__ import annotations

import math
import threading
import time

import pygame

from world_state import world

# World coords roughly cover x=0..80, y=0..70
WORLD_W = 80.0
WORLD_H = 70.0
MARGIN = 56
PANEL_W = 340
WIDTH = 1280
HEIGHT = 800
FPS = 30
LOG_H = 150

ASPHALT = (48, 52, 58)
LANE = (220, 200, 80)
SIDEWALK = (92, 96, 102)
GRASS = (46, 78, 52)
BAY = (70, 74, 82)
BAY_LINE = (230, 230, 230)
WHITE = (245, 245, 245)
SHADOW = (20, 22, 26)
PANEL_BG = (28, 30, 36)
ACCENT = (120, 190, 255)

CATEGORY_COLOR = {
    "emergency": (220, 50, 50),
    "shipping": (240, 160, 40),
    "rest": (70, 140, 230),
    "big_family": (150, 90, 200),
    "disabled": (60, 180, 120),
}

EVENT_COLOR = {
    "drive": (120, 200, 255),
    "park": (120, 220, 140),
    "leave": (255, 200, 120),
    "auction": (220, 160, 255),
    "fine": (255, 110, 110),
    "accept": (140, 255, 180),
    "refuse": (255, 160, 120),
    "offer": (180, 200, 220),
    "seek": (200, 200, 200),
    "wait": (200, 200, 140),
    "info": (210, 210, 210),
}

STATE_LABEL = {
    "seeking": "seeking",
    "waiting": "waiting",
    "commuting": "driving",
    "parked": "PARKED",
    "holding": "HOLDING",
    "leaving": "leaving",
    "done": "done",
}


def _to_screen(x: float, y: float) -> tuple[int, int]:
    map_w = WIDTH - PANEL_W - 2 * MARGIN
    map_h = HEIGHT - LOG_H - 2 * MARGIN
    sx = MARGIN + (x / WORLD_W) * map_w
    sy = MARGIN + (y / WORLD_H) * map_h
    return int(sx), int(sy)


def _draw_street(surf: pygame.Surface):
    surf.fill(GRASS)
    map_rect = pygame.Rect(
        MARGIN - 20,
        MARGIN - 20,
        WIDTH - PANEL_W - 2 * MARGIN + 40,
        HEIGHT - LOG_H - 2 * MARGIN + 40,
    )
    pygame.draw.rect(surf, SIDEWALK, map_rect, border_radius=18)
    road = map_rect.inflate(-28, -28)
    pygame.draw.rect(surf, ASPHALT, road, border_radius=12)

    # Horizontal + vertical lane dashes through the map
    for y in range(road.top + 40, road.bottom, 70):
        x = road.left + 20
        while x < road.right - 20:
            pygame.draw.line(surf, LANE, (x, y), (min(x + 28, road.right - 20), y), 3)
            x += 48
    for x in range(road.left + 50, road.right, 90):
        y = road.top + 20
        while y < road.bottom - 20:
            pygame.draw.line(surf, LANE, (x, y), (x, min(y + 22, road.bottom - 20)), 3)
            y += 42


def _draw_car(surf: pygame.Surface, car: dict, font: pygame.font.Font):
    x, y = _to_screen(car["x"], car["y"])
    heading = car.get("heading", 0.0)
    color = CATEGORY_COLOR.get(car.get("category"), (180, 180, 180))
    size = car.get("size", "small")
    # Larger sprites so cars are easy to spot on the street.
    scale = {"small": 1.35, "medium": 1.55, "large": 1.9}.get(size, 1.5)

    # Top-down car polygon in local coords (nose points +x)
    body = [
        (-22 * scale, -12 * scale),
        (16 * scale, -12 * scale),
        (24 * scale, -6 * scale),
        (24 * scale, 6 * scale),
        (16 * scale, 12 * scale),
        (-22 * scale, 12 * scale),
    ]
    cabin = [
        (-6 * scale, -8 * scale),
        (10 * scale, -8 * scale),
        (12 * scale, -4 * scale),
        (12 * scale, 4 * scale),
        (10 * scale, 8 * scale),
        (-6 * scale, 8 * scale),
    ]
    wheels = [
        (-12 * scale, -14 * scale),
        (10 * scale, -14 * scale),
        (-12 * scale, 14 * scale),
        (10 * scale, 14 * scale),
    ]

    def rot(pts):
        out = []
        c, s = math.cos(heading), math.sin(heading)
        for px, py in pts:
            rx = px * c - py * s
            ry = px * s + py * c
            out.append((x + rx, y + ry))
        return out

    # Halo so cars never blend into asphalt
    pygame.draw.circle(surf, (255, 255, 255), (x, y), int(20 * scale), 2)

    shadow = [(px + 3, py + 3) for px, py in rot(body)]
    pygame.draw.polygon(surf, SHADOW, shadow)
    pygame.draw.polygon(surf, color, rot(body))
    pygame.draw.polygon(surf, WHITE, rot(body), 2)
    pygame.draw.polygon(surf, (30, 40, 55), rot(cabin))
    for wx, wy in rot(wheels):
        pygame.draw.circle(surf, (15, 15, 15), (int(wx), int(wy)), int(4 * scale))
    nose = rot([(22 * scale, -4 * scale), (22 * scale, 4 * scale)])
    for hx, hy in nose:
        pygame.draw.circle(surf, (255, 240, 160), (int(hx), int(hy)), 3)

    label = font.render(car["name"], True, WHITE)
    bg = pygame.Surface((label.get_width() + 6, label.get_height() + 2), pygame.SRCALPHA)
    bg.fill((0, 0, 0, 150))
    surf.blit(bg, (x - label.get_width() // 2 - 3, y + int(16 * scale)))
    surf.blit(label, (x - label.get_width() // 2, y + int(16 * scale)))

    state = car.get("state", "")
    if state:
        st = font.render(state, True, (255, 230, 120))
        surf.blit(st, (x - st.get_width() // 2, y - int(34 * scale)))


def _draw_curb(surf: pygame.Surface, curb: dict, font: pygame.font.Font, now: float):
    x, y = _to_screen(curb["x"], curb["y"])
    cap = max(1, curb.get("capacity", 1))
    w, h = 56 + 18 * (cap - 1), 44

    bay = pygame.Rect(0, 0, w, h)
    bay.center = (x, y)
    pygame.draw.rect(surf, BAY, bay, border_radius=6)
    pygame.draw.rect(surf, BAY_LINE, bay, 2, border_radius=6)
    # Stall dividers
    for i in range(1, cap):
        sx = bay.left + i * (w // cap)
        pygame.draw.line(surf, BAY_LINE, (sx, bay.top + 4), (sx, bay.bottom - 4), 1)

    title = font.render(curb["name"], True, WHITE)
    surf.blit(title, (bay.centerx - title.get_width() // 2, bay.top - 18))

    if curb.get("has_ev"):
        ev = font.render("EV", True, (120, 255, 160))
        surf.blit(ev, (bay.right + 4, bay.centery - 6))

    parked = curb.get("parked") or []
    occ = len(parked)
    occ_txt = font.render(f"{occ}/{cap}", True, ACCENT)
    surf.blit(occ_txt, (bay.centerx - occ_txt.get_width() // 2, bay.bottom + 2))

    # Per-vehicle remaining timers under the bay
    for i, p in enumerate(parked[:3]):
        left = p.get("declared_time", 20) - (now - p.get("parked_at", now))
        left = max(0, left)
        car_name = str(p.get("car", "?")).split("@")[0]
        color = (255, 120, 120) if left < 5 else (180, 220, 140)
        t = font.render(f"{car_name}: {left:0.0f}s", True, color)
        surf.blit(t, (bay.left, bay.bottom + 18 + i * 14))


def _draw_panel(surf: pygame.Surface, snap: dict, fonts: dict):
    panel = pygame.Rect(WIDTH - PANEL_W, 0, PANEL_W, HEIGHT - LOG_H)
    pygame.draw.rect(surf, PANEL_BG, panel)
    pygame.draw.line(surf, (60, 64, 72), panel.topleft, panel.bottomleft, 2)

    title = fonts["title"].render("SPADE Live Map", True, WHITE)
    surf.blit(title, (panel.x + 16, 18))
    elapsed = fonts["body"].render(f"simulation t = {snap['elapsed']:.0f}s", True, ACCENT)
    surf.blit(elapsed, (panel.x + 16, 50))

    y = 86
    surf.blit(fonts["small"].render("Agent status (from SPADE)", True, (170, 170, 170)), (panel.x + 16, y))
    y += 22
    for car in sorted(snap["cars"].values(), key=lambda c: c["name"]):
        color = CATEGORY_COLOR.get(car["category"], WHITE)
        pygame.draw.circle(surf, color, (panel.x + 24, y + 7), 6)
        state = STATE_LABEL.get(car.get("state"), car.get("state", "?"))
        line = f"{car['name']}  [{state}]"
        if car.get("target_curb") and car.get("state") in ("commuting", "parked", "holding"):
            line += f" @ {car['target_curb']}"
        surf.blit(fonts["small"].render(line, True, WHITE), (panel.x + 38, y))
        y += 16
        info = str(car.get("info") or "")
        if info:
            surf.blit(fonts["tiny"].render(info[:42], True, (160, 170, 180)), (panel.x + 38, y))
            y += 14
        y += 4
        if y > HEIGHT - LOG_H - 50:
            break

    hint = fonts["tiny"].render("Close window to stop", True, (120, 120, 120))
    surf.blit(hint, (panel.x + 16, HEIGHT - LOG_H - 28))


def _draw_event_log(surf: pygame.Surface, snap: dict, fonts: dict):
    log = pygame.Rect(0, HEIGHT - LOG_H, WIDTH, LOG_H)
    pygame.draw.rect(surf, (22, 24, 28), log)
    pygame.draw.line(surf, (70, 74, 82), log.topleft, log.topright, 2)
    surf.blit(fonts["body"].render("Live agent events", True, ACCENT), (16, HEIGHT - LOG_H + 10))

    x_col = 16
    y = HEIGHT - LOG_H + 38
    for i, ev in enumerate(snap.get("events") or []):
        if i >= 7:
            break
        color = EVENT_COLOR.get(ev.get("kind"), WHITE)
        line = f"t={ev['t']:.0f}s  {ev['text']}"
        surf.blit(fonts["tiny"].render(line[:110], True, color), (x_col, y))
        y += 15


def _draw_leave_toasts(surf: pygame.Surface, snap: dict, fonts: dict):
    """Red banner in the bottom-right corner of the street map."""
    toasts = snap.get("toasts") or []
    if not toasts:
        return

    map_right = WIDTH - PANEL_W - 24
    map_bottom = HEIGHT - LOG_H - 24
    y = map_bottom
    for toast in reversed(toasts[-3:]):
        label = fonts["body"].render(toast["text"], True, WHITE)
        pad_x, pad_y = 14, 10
        box = pygame.Rect(0, 0, label.get_width() + 2 * pad_x, label.get_height() + 2 * pad_y)
        box.bottomright = (map_right, y)
        pygame.draw.rect(surf, (190, 30, 40), box, border_radius=8)
        pygame.draw.rect(surf, (255, 220, 220), box, 2, border_radius=8)
        surf.blit(label, (box.x + pad_x, box.y + pad_y))
        y = box.top - 8


def run_viz():
    pygame.init()
    pygame.display.set_caption("Decentralised Curbside Parking — Live View")
    screen = pygame.display.set_mode((WIDTH, HEIGHT))
    clock = pygame.time.Clock()
    fonts = {
        "title": pygame.font.SysFont("Avenir Next", 26, bold=True),
        "body": pygame.font.SysFont("Avenir Next", 18),
        "small": pygame.font.SysFont("Avenir Next", 14),
        "tiny": pygame.font.SysFont("Menlo", 12),
    }
    # Fallback if fancy fonts missing
    for k in list(fonts):
        if fonts[k] is None:
            fonts[k] = pygame.font.Font(None, 20)

    while True:
        dt = clock.tick(FPS) / 1000.0
        # Drive cars toward curb coordinates every frame (smooth, continuous motion).
        world.advance_motion(dt)

        snap = world.snapshot()
        if not snap["running"]:
            break

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                world.stop()
                break

        _draw_street(screen)
        now = time.time()
        for curb in snap["curbs"].values():
            _draw_curb(screen, curb, fonts["tiny"], now)
        for car in snap["cars"].values():
            if car.get("state") == "done":
                continue
            _draw_car(screen, car, fonts["tiny"])
        active = sum(1 for c in snap["cars"].values() if c.get("state") != "done")
        count = fonts["body"].render(
            f"active cars: {active}/{len(snap['cars'])}   curbs: {len(snap['curbs'])}",
            True,
            WHITE,
        )
        screen.blit(count, (MARGIN, 16))
        _draw_panel(screen, snap, fonts)
        _draw_event_log(screen, snap, fonts)
        _draw_leave_toasts(screen, snap, fonts)

        pygame.display.flip()

    pygame.quit()


def start_viz_thread() -> threading.Thread:
    t = threading.Thread(target=run_viz, name="pygame-viz", daemon=True)
    t.start()
    return t
