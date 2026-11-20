# Decentralised Urban Parking and Curbside Management

Multi-agent system built in spade that simulates cars negotiating parking at street curbs: priority scoring, car-to-car spot auctions, commute o the curbs, overstay fines and a live Pygame simulation.

Course project for **Introduction to Intelligent Autonomous Systems (ISIA)** — FCUP

---

## Description

The system models a contested urban curbside. Vehicles and parking bays are autonomous agents that communicate over XMPP. There is **no central planner**: allocation emerges from local negotiation.

**What happens in a run**

1. Each **CarAgent** sends a **CFP** to all curb agents (GPS, size, EV flag, category).
2. Each **CurbAgent** answers with **propose** (score) or **refuse** (full / wrong size / EV rules).
3. The car **accepts** the best offer; the curb **re-checks** capacity (avoids double-booking).
4. The car **commutes** to the  curb, then **parks** for a limited stay.
5. If the preferred curb is full, cars may **bid** a parked peer to hold the spot; the highest bid wins, then **SPOT_READY** and a curb **p2p_handoff**.
6. **Emergency** vehicles get high curb scores and do not join auctions.
7. If a car **overstays** (stay + grace), the curb reports to the **EnforcementAgent**, which issues a fine.
8. On leave, capacity is freed; the Pygame view shows live positions, timers, event log, and leave notifications.

**Scenario** (`config.json`): 5 curbs (mixed capacity / EV), 7 cars (emergency, shipping, rest, family, disabled), 1 enforcer.

**Agent roles**

| Agent              | Role                                                    |
| ------------------ | ------------------------------------------------------- |
| `CarAgent`         | Seek spot, negotiate, auction, commute, park, leave     |
| `CurbAgent`        | Score offers, capacity, LEAVE, handoff, overstay report |
| `EnforcementAgent` | Receive overstay reports, issue 50 fines                |

Console wording maps to FIPA performatives, e.g. `Offer from curb…` = **propose**, `Took curb…` = **accept-proposal**, `Parked…` = park.

---

## Technologies

| Technology                       | Use in this project                                                |
| -------------------------------- | ------------------------------------------------------------------ |
| **Python 3.10+**                 | Language (tested with 3.13)                                        |
| **SPADE 4**                      | Multi-agent framework                                              |
| **XMPP**                         | Messages between agents (port **5222**,s via `spade run`)          |
| **FIPA-ACL-style performatives** | `cfp`, `propose`, `refuse`, `accept-proposal`, `inform`, `request` |
| **Pygame**                       | Live street map visualisation                                      |
| **JSON**                         | Scenario configuration (`config.json`)                             |
| **Shared ontology**              | `ontology.py` — priorities, bids, hold times, sizes, events        |

**Main modules:** `main.py`, `ontology.py`, `world_state.py`, `viz.py`, `agents/CarAgent.py`, `agents/CurbAgent.py`, `agents/EnforcementAgent.py`.

---

## How to run

### Requirements

- Python 3.10 or newer
- Port **5222** free for XMPP 

### Install

```bash
git clone https://gitlab.up.pt/up202512397/autonomoussystems.git
cd autonomoussystems

python -m venv .venv

# macOS / Linux
source .venv/bin/activate

# Windows
# .venv\Scripts\activate

pip install -r requirements.txt
```

### xRun

```bash
# If a previous run left XMPP up (macOS / Linux):
lsof -ti :5222 | xargs kill

python main.py
```

What starts:

1. External XMPP server (`spade run` on **5222**) if needed
2. SPADE agents in a background thread
3. Pygame window on the **main thread** (required on macOS)

Close the Pygame window to stop the visualisation; the simulation then shuts down.

### Troubleshooting

| Problem | What to try |
|---------|-------------|
| Cars stuck on “waiting for SPADE…” | Free port 5222, reinstall deps, check the terminal for agent startup lines |
| Port 5222 busy | `lsof -ti :5222 \| xargs kill` (Windows: end the process listening on 5222) |
| Need a clean re-run | Kill 5222, then `python main.py` again |

---

## Authors
