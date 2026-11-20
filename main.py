import asyncio
import json
import socket
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

import spade
from agents.CarAgent import CarAgent
from agents.CurbAgent import CurbAgentSpade
from agents.EnforcementAgent import EnforcementAgent
from viz import run_viz
from world_state import world


def load_config():
    config_path = Path(__file__).resolve().parent / "config.json"
    return json.loads(config_path.read_text())


def seed_world(config):
    """Register map entities on the main thread before Pygame opens."""
    for curb_conf in config["curb_nodes"]:
        world.register_curb(
            curb_conf["jid"],
            curb_conf["x"],
            curb_conf["y"],
            curb_conf["capacity"],
            curb_conf.get("has_ev_spots", False),
        )
    for vehicle in config["vehicles"]:
        world.register_car(
            vehicle["jid"],
            vehicle["x"],
            vehicle["y"],
            vehicle["category"],
            vehicle["size"],
        )
        world.update_car(vehicle["jid"], info="waiting for SPADE…")


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.3):
            return True
    except OSError:
        return False


def start_xmpp_server() -> subprocess.Popen | None:
    """
    Run SPADE's XMPP server in a separate process.
    Embedded server inside a background thread fails silently on macOS.
    """
    if _port_open("localhost", 5222):
        world.log("XMPP already running on :5222", "info")
        print("XMPP already running on :5222", flush=True)
        return None

    cmd = [sys.executable, "-m", "spade", "run"]
    # Prefer the venv `spade` console script when available.
    spade_bin = Path(sys.executable).with_name("spade")
    if spade_bin.exists():
        cmd = [str(spade_bin), "run"]

    print(f"Starting XMPP server: {' '.join(cmd)}", flush=True)
    world.log("starting XMPP server…", "info")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(50):
        if _port_open("localhost", 5222):
            world.log("XMPP server ready on :5222", "info")
            print("XMPP server ready on :5222", flush=True)
            return proc
        if proc.poll() is not None:
            break
        time.sleep(0.1)

    world.log("XMPP server failed to start — check port 5222", "fine")
    print("ERROR: XMPP server did not open port 5222", flush=True)
    return proc


async def simulation(config):
    world.log("SPADE agents starting…", "info")
    print("SPADE agents starting…", flush=True)

    enforcement = EnforcementAgent(config["enforcer"]["jid"], config["enforcer"]["password"])
    await enforcement.start(auto_register=True)
    world.log("enforcer online", "info")

    curbs = []
    for curb_conf in config["curb_nodes"]:
        curb = CurbAgentSpade(curb_conf["jid"], curb_conf["password"])
        curb.set("x", curb_conf["x"])
        curb.set("y", curb_conf["y"])
        curb.set("capacity", curb_conf["capacity"])
        curb.set("has_ev_spots", curb_conf.get("has_ev_spots", False))
        curb.set("enforcer", config["enforcer"]["jid"])
        await curb.start(auto_register=True)
        curbs.append(curb)
    world.log(f"{len(curbs)} curbs online", "info")
    await asyncio.sleep(2)

    cars = []
    for vehicle in config["vehicles"]:
        if not world.snapshot()["running"]:
            break
        car = CarAgent(vehicle["jid"], vehicle["password"])
        car.set("x", vehicle["x"])
        car.set("y", vehicle["y"])
        car.set("category", vehicle["category"])
        car.set("size", vehicle["size"])
        car.set("is_ev", vehicle["is_ev"])
        car.set("curbs", config["curb_nodes"])
        car.set("peers", [o["jid"] for o in config["vehicles"] if o["jid"] != vehicle["jid"]])
        await car.start(auto_register=True)
        cars.append(car)
        await asyncio.sleep(4)

    remaining = 30 + 20 * max(len(cars), 1)
    while remaining > 0 and world.snapshot()["running"]:
        await asyncio.sleep(1)
        remaining -= 1

    for car in cars:
        await car.stop()
    for curb in curbs:
        await curb.stop()
    await enforcement.stop()
    world.stop()
    print("End of the simulation. All agents have stopped.", flush=True)
    world.log("simulation finished", "info")


def _run_simulation(config):
    try:
        # IMPORTANT: no embedded server in a thread — use external `spade run`.
        spade.run(simulation(config), embedded_xmpp_server=False)
    except Exception as exc:
        traceback.print_exc()
        world.log(f"SPADE crashed: {exc}", "fine")
        world.stop()


if __name__ == "__main__":
    config = load_config()
    seed_world(config)

    xmpp_proc = start_xmpp_server()
    if not _port_open("localhost", 5222):
        print("Cannot start without XMPP. Try: lsof -ti :5222 | xargs kill", flush=True)
        world.log("no XMPP — agents cannot start", "fine")

    sim_thread = threading.Thread(
        target=_run_simulation, args=(config,), name="spade-sim", daemon=True
    )
    sim_thread.start()

    # Pygame must own the macOS main thread.
    run_viz()
    world.stop()
    sim_thread.join(timeout=5)
    if xmpp_proc is not None and xmpp_proc.poll() is None:
        xmpp_proc.terminate()
