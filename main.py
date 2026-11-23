import asyncio
import json
import logging
import os
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
    busy_ports = [port for port in (5222, 9090) if _port_open("localhost", port)]
    if busy_ports:
        raise RuntimeError(
            f"SPADE ports already in use: {', '.join(map(str, busy_ports))}. "
            "Stop the old SPADE server, then run main.py again."
        )

    cmd = [sys.executable, "-m", "spade", "run"]
    # Prefer the venv `spade` console script when available.
    spade_name = "spade.exe" if sys.platform == "win32" else "spade"
    spade_bin = Path(sys.executable).with_name(spade_name)
    if spade_bin.exists():
        cmd = [str(spade_bin), "run"]
    cmd.extend(["--host", "localhost", "--memory"])
    if os.environ.get("SPADE_DEBUG") == "1":
        cmd.append("--debug")

    print(f"Starting XMPP server: {' '.join(cmd)}", flush=True)
    world.log("starting XMPP server…", "info")
    proc = subprocess.Popen(cmd)
    for _ in range(150):
        if _port_open("localhost", 5222) and _port_open("localhost", 9090):
            world.log("XMPP server ready on :5222", "info")
            print("XMPP server ready on :5222", flush=True)
            return proc
        if proc.poll() is not None:
            break
        time.sleep(0.1)

    if proc.poll() is None:
        proc.terminate()
        proc.wait(timeout=5)
    raise RuntimeError("SPADE did not open ports 5222 and 9090; see server output above.")


async def _start_agent(agent, name: str):
    print(f"Starting {name}...", flush=True)
    try:
        await asyncio.wait_for(agent.start(auto_register=True), timeout=20)
    except asyncio.TimeoutError as exc:
        raise TimeoutError(f"{name} did not connect to XMPP within 20 seconds.") from exc
    print(f"{name} online", flush=True)


async def simulation(config):
    world.log("SPADE agents starting…", "info")
    print("SPADE agents starting…", flush=True)

    enforcement = EnforcementAgent(config["enforcer"]["jid"], config["enforcer"]["password"])
    await _start_agent(enforcement, "enforcer")
    world.log("enforcer online", "info")

    curbs = []
    for curb_conf in config["curb_nodes"]:
        curb = CurbAgentSpade(curb_conf["jid"], curb_conf["password"])
        curb.set("x", curb_conf["x"])
        curb.set("y", curb_conf["y"])
        curb.set("capacity", curb_conf["capacity"])
        curb.set("has_ev_spots", curb_conf.get("has_ev_spots", False))
        curb.set("enforcer", config["enforcer"]["jid"])
        await _start_agent(curb, curb_conf["jid"])
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
        await _start_agent(car, vehicle["jid"])
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
        if os.environ.get("SPADE_DEBUG") == "1":
            logging.basicConfig(level=logging.DEBUG)
            logging.getLogger("slixmpp").setLevel(logging.DEBUG)
        # Keep SPADE on the main thread on Windows for its event-loop backend.
        spade.run(simulation(config), embedded_xmpp_server=False)
    except Exception as exc:
        traceback.print_exc()
        world.log(f"SPADE crashed: {exc}", "fine")
        world.stop()


def _run_viz():
    try:
        run_viz()
    finally:
        world.stop()


if __name__ == "__main__":
    config = load_config()
    seed_world(config)

    try:
        xmpp_proc = start_xmpp_server()
    except (OSError, RuntimeError) as exc:
        print(f"Cannot start simulation: {exc}", flush=True)
        world.log(f"cannot start simulation: {exc}", "fine")
        raise SystemExit(1)

    try:
        if sys.platform == "win32":
            viz_thread = threading.Thread(target=_run_viz, name="pygame-viz", daemon=True)
            viz_thread.start()
            _run_simulation(config)
            viz_thread.join(timeout=5)
        else:
            sim_thread = threading.Thread(
                target=_run_simulation, args=(config,), name="spade-sim", daemon=True
            )
            sim_thread.start()
            # Pygame must own the macOS main thread.
            _run_viz()
            sim_thread.join(timeout=5)
    finally:
        world.stop()
        if xmpp_proc.poll() is None:
            xmpp_proc.terminate()
            xmpp_proc.wait(timeout=5)
