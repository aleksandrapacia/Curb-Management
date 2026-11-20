import time
import json
from spade.agent import Agent
from spade.behaviour import CyclicBehaviour
from spade.message import Message
from ontology import *
from world_state import world


class CurbAgent:
    """Manages curbside parking slot constraints, scoring, priority preemption, and P2P handoffs."""

    def __init__(self, curb_id: str, capacity_spaces: int = 1, is_ev_only: bool = False,
                 location: tuple = (0.0, 0.0), has_ev_spots: bool = False):
        self.curb_id = curb_id
        self.capacity_spaces = capacity_spaces
        self.is_ev_only = is_ev_only        # hard rule: only EVs allowed
        self.has_ev_spots = has_ev_spots    # soft rule: EVs get a score bonus
        self.location = location

        self.occupied_slots = 0
        self.parked_vehicles = {}

    @staticmethod
    def _bare(jid) -> str:
        return str(jid).split("/")[0]

    def _find_parked(self, car_id: str):
        """Return (stored_key, data) matching bare JID, or (None, None)."""
        target = self._bare(car_id)
        for key, data in self.parked_vehicles.items():
            if self._bare(key) == target:
                return key, data
        return None, None

    def sync_world(self):
        """Push occupancy + per-car timers to the Pygame view."""
        parked = [
            {
                "car": self._bare(car_id),
                "parked_at": data.get("parked_at", time.time()),
                "declared_time": data.get("declared_time", STAY_SECONDS),
                "category": data.get("category", "rest"),
            }
            for car_id, data in self.parked_vehicles.items()
        ]
        world.update_curb(self.curb_id, self.occupied_slots, parked)

    def _free_spaces(self) -> int:
        return self.capacity_spaces - self.occupied_slots

    # ---------- CFP ----------
    def cfp_menagement(self, message: dict) -> dict:
        """Handles incoming CFP messages from cars and evaluates slot availability."""
        car_id = message.get("sender")
        car_data = message.get("content", {})
        car_size = car_data.get("size", "medium")
        is_ev = car_data.get("is_ev", False)
        category = car_data.get("category", "rest")
        car_gps = car_data.get("gps", (0.0, 0.0))
        is_emergency = (category == "emergency")

        required_spaces = SIZE_SPACES.get(car_size, 1)

        if required_spaces > self._free_spaces() and not is_emergency:
            return self._create_refuse(car_id, "There's no free slot for this size")

        if self.is_ev_only and not is_ev and not is_emergency:
            return self._create_refuse(car_id, "Slot only for EV vehicles")

        score = self._calculate_score(car_gps, category, is_emergency, is_ev)
        return self._create_propose(car_id, score)

    def _calculate_score(self, car_gps: tuple, category: str,
                         is_emergency: bool = False, is_ev: bool = False) -> float:
        """Proposal score = priority + distance + EV bonus."""
        priority_rank = PRIORITIES.get(category, 5)
        priority_score = (10 - priority_rank) * 100

        if is_emergency:
            priority_score += 10000

        distance = ((self.location[0] - car_gps[0]) ** 2 + (self.location[1] - car_gps[1]) ** 2) ** 0.5
        # 100 at distance 0, ~50 at distance 10, ~17 at distance 50 (never collapses to 0)
        distance_score = 100 / (1 + distance / 10)

        ev_bonus = EV_BONUS if (is_ev and self.has_ev_spots) else 0
        return priority_score + distance_score + ev_bonus

    # ---------- ACCEPT ----------
    def handle_accept_proposal(self, message: dict) -> dict:
        """Registers a car once it accepts the proposal, if the slot is still free."""
        car_id = self._bare(message.get("sender"))
        car_data = message.get("content", {})
        category = car_data.get("category", "rest")
        required_spaces = SIZE_SPACES.get(car_data.get("size", "medium"), 1)

        # Duplicate accept from a car that is already parked here.
        key, _ = self._find_parked(car_id)
        if key is not None:
            return {"performative": PERFORMATIVE_INFORM, "status": "confirmed", "car_id": car_id}

        # Re-check capacity: the slot may have been taken after the proposal was made.
        if required_spaces > self._free_spaces() and category != "emergency":
            return {
                "performative": PERFORMATIVE_REFUSE,
                "status": "slot_taken",
                "car_id": car_id,
                "reason": "Slot was taken before your acceptance arrived.",
            }

        self.occupied_slots += required_spaces
        self.parked_vehicles[car_id] = {
            "parked_at": time.time(),
            "declared_time": car_data.get("declared_time", STAY_SECONDS),
            "category": category,
            "spaces": required_spaces,
            "reported_overstay": False,
        }
        self.sync_world()
        return {"performative": PERFORMATIVE_INFORM, "status": "confirmed", "car_id": car_id}

    # ---------- LEAVE / EXTEND ----------
    def handle_leave(self, message: dict) -> dict:
        """Frees the slot when a car announces departure."""
        content = message.get("content", {})
        car_id = content.get("car") or message.get("sender")
        key, data = self._find_parked(car_id)
        if key is None:
            return {
                "performative": PERFORMATIVE_REFUSE,
                "status": "unknown_car",
                "reason": "Car was not registered as parked.",
            }

        spaces = data.get("spaces", 1)
        self.parked_vehicles.pop(key)
        self.occupied_slots = max(0, self.occupied_slots - spaces)
        self.sync_world()
        return {
            "performative": PERFORMATIVE_INFORM,
            "status": "departed",
            "car_id": self._bare(key),
        }

    def handle_extend(self, message: dict) -> None:
        """A parked car stays longer on purpose (it is holding the spot for a buyer)."""
        content = message.get("content", {})
        key, data = self._find_parked(content.get("car") or message.get("sender"))
        if key is not None:
            data["declared_time"] += float(content.get("extra", 0))
            self.sync_world()

    # ---------- OVERSTAY ----------
    def check_overstays(self) -> list:
        """Cars that stayed longer than declared time + grace. Emergency vehicles are exempt."""
        now = time.time()
        overstayed = []
        for car_id, data in self.parked_vehicles.items():
            if data.get("reported_overstay", False):
                continue
            if data.get("category", "rest") == "emergency":
                continue
            max_allowed = data.get("declared_time", STAY_SECONDS) + OVERSTAY_GRACE
            if (now - data["parked_at"]) > max_allowed:
                data["reported_overstay"] = True
                overstayed.append(car_id)
        return overstayed

    # ---------- P2P handoff ----------
    def handle_p2p_handoff(self, message: dict) -> dict:
        """Executes slot handoff between the current owner and the winning car."""
        content = message.get("content", {})
        old_car_id = content.get("old_owner") or content.get("old_car")
        new_car_id = self._bare(content.get("new_car"))
        key, car_info = self._find_parked(old_car_id)

        if key is None:
            return {
                "performative": PERFORMATIVE_REFUSE,
                "status": "handoff_failed",
                "reason": "Old car not found on curb during handoff.",
            }

        # The new car may be a different size than the old one.
        new_spaces = SIZE_SPACES.get(content.get("size", "medium"), 1)
        old_spaces = car_info.get("spaces", 1)
        extra = new_spaces - old_spaces
        if extra > self._free_spaces():
            return {
                "performative": PERFORMATIVE_REFUSE,
                "status": "handoff_failed",
                "reason": "New car needs more space than the old one frees.",
            }

        self.parked_vehicles.pop(key)
        self.occupied_slots = max(0, self.occupied_slots + extra)
        self.parked_vehicles[new_car_id] = {
            "parked_at": time.time(),
            "declared_time": content.get("declared_time", STAY_SECONDS),
            "category": content.get("category", "rest"),
            "spaces": new_spaces,
            "reported_overstay": False,
        }
        self.sync_world()
        return {
            "performative": PERFORMATIVE_INFORM,
            "status": "handoff_success",
            "new_car": new_car_id,
        }

    # ---------- message builders ----------
    def _create_propose(self, recipient: str, score: float) -> dict:
        return {
            "performative": PERFORMATIVE_PROPOSE,
            "sender": self.curb_id,
            "receiver": recipient,
            "ontology": ONTOLOGY_PARKING,
            "content": {"score": score, "curb_id": self.curb_id},
        }

    def _create_refuse(self, recipient: str, reason: str) -> dict:
        return {
            "performative": PERFORMATIVE_REFUSE,
            "sender": self.curb_id,
            "receiver": recipient,
            "ontology": ONTOLOGY_PARKING,
            "content": {"reason": reason},
        }


class CurbAgentSpade(Agent):
    """SPADE network layer responsible for receiving and sending XMPP messages to other agents."""

    class CurbBehaviour(CyclicBehaviour):
        async def run(self):
            msg = await self.receive(timeout=2)
            if msg:
                performative = msg.get_metadata("performative")
                try:
                    content = json.loads(msg.body) if msg.body else {}
                except json.JSONDecodeError:
                    content = {}

                incoming_msg = {"sender": str(msg.sender), "content": content}

                response = None
                if performative == PERFORMATIVE_CFP:
                    response = self.agent.logic.cfp_menagement(incoming_msg)
                elif performative == PERFORMATIVE_ACCEPT:
                    response = self.agent.logic.handle_accept_proposal(incoming_msg)
                elif performative == PERFORMATIVE_REQUEST:
                    if content.get("action") == "p2p_handoff":
                        response = self.agent.logic.handle_p2p_handoff(incoming_msg)
                elif performative == PERFORMATIVE_INFORM:
                    event = content.get("event")
                    if event == "LEAVE":
                        response = self.agent.logic.handle_leave(incoming_msg)
                    elif event == EVENT_EXTEND:
                        self.agent.logic.handle_extend(incoming_msg)

                if response:
                    reply = Message(to=str(msg.sender))
                    reply.set_metadata("performative", response.get("performative", PERFORMATIVE_INFORM))
                    reply.body = json.dumps(response)
                    await self.send(reply)

            # Keep curb timers fresh for the live map.
            self.agent.logic.sync_world()

            # Report each overstay to the enforcer once per parking session.
            enforcer = self.agent.get("enforcer") or "enforcer1@localhost"
            for offending_car in self.agent.logic.check_overstays():
                car_name = str(offending_car).split("@")[0]
                print(f"[{self.agent.name}] {car_name} overstayed. Reporting to enforcer.")
                world.log(f"{self.agent.name}: {car_name} overstay → enforcer", "fine")
                fine_msg = Message(to=enforcer)
                fine_msg.set_metadata("ontology", ONTOLOGY_PARKING)
                fine_msg.set_metadata("performative", PERFORMATIVE_REQUEST)
                fine_msg.body = json.dumps({
                    "action": "issue_fine",
                    "car_id": offending_car,
                    "curb_id": str(self.agent.jid),
                })
                await self.send(fine_msg)

    async def setup(self):
        x = float(self.get("x") or 0.0)
        y = float(self.get("y") or 0.0)
        capacity = int(self.get("capacity") or 1)
        has_ev = bool(self.get("has_ev_spots"))
        self.logic = CurbAgent(
            curb_id=str(self.jid),
            capacity_spaces=capacity,
            location=(x, y),
            has_ev_spots=has_ev,
        )
        print(f"{self.name} ready at ({x}, {y}), capacity={capacity}, ev_spots={has_ev}")
        world.register_curb(str(self.jid), x, y, capacity, has_ev)
        self.logic.sync_world()
        self.add_behaviour(self.CurbBehaviour())