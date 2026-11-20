"""Shared live state between SPADE agents and the Pygame view."""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from copy import deepcopy


class WorldState:
    def __init__(self):
        self._lock = threading.Lock()
        self.cars: dict[str, dict] = {}
        self.curbs: dict[str, dict] = {}
        self.events: deque[dict] = deque(maxlen=40)
        self.toasts: list[dict] = []
        self.running = True
        self.started_at = time.time()

    def log(self, text: str, kind: str = "info"):
        """Push a short status line for the live event feed."""
        with self._lock:
            self.events.appendleft({
                "t": time.time() - self.started_at,
                "text": str(text),
                "kind": kind,
            })

    def toast(self, text: str, seconds: float = 4.0):
        """Show a temporary banner on the map (e.g. car left parking)."""
        with self._lock:
            self.toasts.append({
                "text": str(text),
                "until": time.time() + float(seconds),
            })

    def active_toasts(self) -> list[dict]:
        now = time.time()
        with self._lock:
            self.toasts = [t for t in self.toasts if t["until"] > now]
            return list(self.toasts)

    def register_curb(self, jid: str, x: float, y: float, capacity: int, has_ev: bool = False):
        name = str(jid).split("@")[0]
        with self._lock:
            self.curbs[name] = {
                "jid": str(jid).split("/")[0],
                "name": name,
                "x": float(x),
                "y": float(y),
                "capacity": int(capacity),
                "has_ev": bool(has_ev),
                "occupied": 0,
                "parked": [],
            }

    def register_car(self, jid: str, x: float, y: float, category: str, size: str):
        name = str(jid).split("@")[0]
        with self._lock:
            existing = self.cars.get(name)
            if existing:
                existing["category"] = category or existing["category"]
                existing["size"] = size or existing["size"]
                return
            self.cars[name] = {
                "jid": str(jid).split("/")[0],
                "name": name,
                "x": float(x),
                "y": float(y),
                "home_x": float(x),
                "home_y": float(y),
                "heading": 0.0,
                "category": category or "rest",
                "size": size or "small",
                "state": "seeking",
                "target_curb": None,
                "target_x": None,
                "target_y": None,
                "park_deadline": None,
                "speed": 14.0,
                "info": "waiting to start",
            }

    def update_car(
        self,
        jid: str,
        *,
        x: float | None = None,
        y: float | None = None,
        heading: float | None = None,
        state: str | None = None,
        target_curb: str | None = None,
        target_x: float | None = None,
        target_y: float | None = None,
        park_deadline: float | None = None,
        clear_deadline: bool = False,
        speed: float | None = None,
        info: str | None = None,
        clear_target: bool = False,
    ):
        name = str(jid).split("@")[0]
        with self._lock:
            car = self.cars.get(name)
            if not car:
                return
            if x is not None:
                car["x"] = float(x)
            if y is not None:
                car["y"] = float(y)
            if heading is not None:
                car["heading"] = float(heading)
            if state is not None:
                car["state"] = state
            if target_curb is not None:
                car["target_curb"] = str(target_curb).split("@")[0]
            if target_x is not None:
                car["target_x"] = float(target_x)
            if target_y is not None:
                car["target_y"] = float(target_y)
            if clear_target:
                car["target_x"] = None
                car["target_y"] = None
                car["target_curb"] = None
            if speed is not None:
                car["speed"] = float(speed)
            if info is not None:
                car["info"] = info
            if clear_deadline:
                car["park_deadline"] = None
            elif park_deadline is not None:
                car["park_deadline"] = float(park_deadline)

    def update_curb(self, jid: str, occupied: int, parked: list[dict]):
        name = str(jid).split("@")[0]
        with self._lock:
            curb = self.curbs.get(name)
            if not curb:
                return
            curb["occupied"] = int(occupied)
            curb["parked"] = list(parked)

    def advance_motion(self, dt: float):
        """
        Move cars only when SPADE says they are commuting or leaving.
        Seeking = wait at spawn. Parked/holding = stay on the curb.
        """
        with self._lock:
            for car in self.cars.values():
                state = car.get("state", "seeking")

                if state in ("seeking", "waiting", "done"):
                    # Idle at current / home position — no random roaming.
                    continue

                if state in ("parked", "holding"):
                    curb_name = car.get("target_curb")
                    curb = self.curbs.get(str(curb_name).split("@")[0]) if curb_name else None
                    if curb:
                        car["x"], car["y"] = curb["x"], curb["y"]
                    continue

                tx, ty = car.get("target_x"), car.get("target_y")
                if (tx is None or ty is None) and car.get("target_curb"):
                    curb = self.curbs.get(str(car["target_curb"]).split("@")[0])
                    if curb:
                        tx, ty = curb["x"], curb["y"]
                        car["target_x"], car["target_y"] = tx, ty

                if tx is None or ty is None:
                    continue

                dx, dy = tx - car["x"], ty - car["y"]
                dist = math.hypot(dx, dy)
                if dist < 0.25:
                    car["x"], car["y"] = tx, ty
                    if state == "leaving":
                        car["state"] = "done"
                        car["info"] = "left the map"
                    continue

                speed = float(car.get("speed") or 14.0)
                if state == "leaving":
                    speed *= 1.15
                step = min(speed * dt, dist)
                car["x"] += dx / dist * step
                car["y"] += dy / dist * step
                car["heading"] = math.atan2(dy, dx)

    def stop(self):
        with self._lock:
            self.running = False

    def snapshot(self) -> dict:
        with self._lock:
            now = time.time()
            self.toasts = [t for t in self.toasts if t["until"] > now]
            return {
                "cars": deepcopy(self.cars),
                "curbs": deepcopy(self.curbs),
                "events": list(self.events),
                "toasts": list(self.toasts),
                "running": self.running,
                "elapsed": now - self.started_at,
            }


world = WorldState()
