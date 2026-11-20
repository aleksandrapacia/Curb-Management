import asyncio
import json
import math
import time

from spade.agent import Agent
from spade.behaviour import CyclicBehaviour, OneShotBehaviour
from spade.message import Message
from ontology import *
from world_state import world

SHOW_PEER_LOG = True   # print "X parked at curbN" once per car
MAX_ATTEMPTS = 3       # how many CFP rounds a car tries before giving up


def _bare(jid):
    return str(jid).split("/")[0]


def _short(jid):
    return str(jid).split("@")[0]


def _payload(msg):
    if not msg or not msg.body:
        return {}
    try:
        data = json.loads(msg.body)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


class CarAgent(Agent):
    async def setup(self):
        print(f"{self.name} started.")
        self.x = float(self.get("x") or 0)
        self.y = float(self.get("y") or 0)
        self.category = self.get("category")
        self.priority = PRIORITIES.get(self.category, PRIORITIES["rest"])
        self.is_emergency = self.category == "emergency"
        self.size = self.get("size")
        self.size_units = SIZE_SPACES.get(self.size, 1)
        self.is_ev = bool(self.get("is_ev"))
        self.curbs = list(self.get("curbs") or [])
        self.peers = list(self.get("peers") or [])
        # Emergency never bids. Other cars may still offer money to an ambulance.
        self.bid = 0 if self.is_emergency else BIDS.get(self.category, 0)
        self.hold = 0 if self.is_emergency else HOLD_SECONDS.get(self.category, 0)
        self.bids_sent = set()
        self.registered_as = str(self.jid)
        self.refused = set()
        self.heard = {}
        self.debt = 0.0
        # seeking | commuting | parked | leaving | done
        self.state = "seeking"
        self.target_curb = None
        world.register_car(str(self.jid), self.x, self.y, self.category, self.size)
        world.update_car(str(self.jid), state="seeking")
        # Inbox has no template, so it gets a copy of every message and also
        # swallows the ones nobody else wants (no more "No behaviour matched").
        self.add_behaviour(self.Inbox())
        self.add_behaviour(self.AskOnce())
        self._log("started — asking curbs for a spot", "seek")
        self._publish(state="seeking", info="asking curbs")

    def _publish(self, **kwargs):
        world.update_car(str(self.jid), **kwargs)

    def _log(self, text: str, kind: str = "info"):
        world.log(f"{self.name}: {text}", kind)

    class Inbox(CyclicBehaviour):
        """Logs fines and PARKED notices in one clean line each."""

        async def on_start(self):
            self.seen = set()

        async def run(self):
            msg = await self.receive(timeout=5)
            if not msg:
                return
            data = _payload(msg)

            if data.get("event") == EVENT_PARKED and SHOW_PEER_LOG:
                key = (data.get("car"), data.get("curb"))
                if key not in self.seen:
                    self.seen.add(key)
                    print(f"[{self.agent.name}] sees: {_short(data.get('car'))} "
                          f"parked at {_short(data.get('curb'))}")

            elif data.get("action") == "fine_issued":
                self.agent.debt = data.get("total_debt", self.agent.debt)
                print(f"[{self.agent.name}] Fine: ${data.get('amount')} "
                      f"({data.get('reason')}). Total debt: ${data.get('total_debt')}.")
                self.agent._log(
                    f"FINE ${data.get('amount')} — {data.get('reason')}",
                    "fine",
                )

    class AskOnce(OneShotBehaviour):
        async def run(self):
            offers = await self._request_offers()

            # Emergency never joins an auction. Other cars may still pay it to wait.
            if not self.agent.is_emergency:
                await self._ask_parked_car()

            # If this car bid, wait for the result before taking any free curb.
            if self.agent.bids_sent:
                print(f"[{self.agent.name}] Bid sent. Waiting for the parked car to decide.")
                self.agent.state = "waiting"
                self.agent._publish(state="waiting", info="waiting for auction result")
                self.agent._log("waiting for auction result", "auction")
                ready = await self._wait_for_spot(45)
                if ready:
                    curb_jid = ready.get("curb")
                    await self._commute(curb_jid)
                    await self._park(curb_jid, bought=ready.get("amount"))
                    return
                print(f"[{self.agent.name}] Auction lost or timed out. Checking free curbs.")
                self.agent._log("auction lost / timeout — looking for free curb", "auction")
                self.agent.state = "seeking"
                self.agent._publish(state="seeking", info="auction lost, seeking again")
            elif not offers:
                if self.agent.is_emergency:
                    print(f"[{self.agent.name}] Emergency priority: no offer yet.")
                    return
                print(f"[{self.agent.name}] No free spot. Waiting for a car to hold one.")
                self.agent.state = "waiting"
                self.agent._publish(state="waiting", info="waiting for a held spot")
                self.agent._log("no free spot — waiting for handoff", "wait")
                ready = await self._wait_for_spot(45)
                if ready:
                    curb_jid = ready.get("curb")
                    await self._commute(curb_jid)
                    await self._park(curb_jid, bought=ready.get("amount"))
                    return
                print(f"[{self.agent.name}] No offer.")
                self.agent._log("gave up — no spot", "refuse")
                return

            # Try the best offer first. If the curb says the slot is gone
            # (someone accepted before us), go to the next one, then re-ask.
            for attempt in range(MAX_ATTEMPTS):
                if attempt > 0:
                    print(f"[{self.agent.name}] Asking the curbs again ({attempt + 1}/{MAX_ATTEMPTS}).")
                    offers = await self._request_offers()
                if offers and await self._take_best(offers):
                    return
                await asyncio.sleep(1)
            print(f"[{self.agent.name}] Could not get any curb.")

        # ---------- asking and accepting ----------
        async def _request_offers(self):
            body = {
                "size": self.agent.size,
                "is_ev": self.agent.is_ev,
                "category": self.agent.category,
                "gps": [self.agent.x, self.agent.y],
            }
            for curb in self.agent.curbs:
                msg = Message(to=curb["jid"])
                msg.set_metadata("ontology", ONTOLOGY_PARKING)
                msg.set_metadata("performative", PERFORMATIVE_CFP)
                msg.body = json.dumps(body)
                await self.send(msg)
            print(f"[{self.agent.name}] Asked {len(self.agent.curbs)} curbs for a spot.")

            offers = []
            heard_from = set()
            deadline = asyncio.get_running_loop().time() + 5
            while asyncio.get_running_loop().time() < deadline and len(heard_from) < len(self.agent.curbs):
                msg = await self.receive(timeout=1)
                if not msg:
                    continue
                kind = msg.get_metadata("performative")
                payload = _payload(msg)
                if kind == PERFORMATIVE_REFUSE:
                    reason = payload.get("content", {}).get("reason", "no reason given")
                    print(f"[{self.agent.name}] {_short(msg.sender)} refused: {reason}")
                    self.agent._log(f"refused by {_short(msg.sender)} — {reason}", "refuse")
                    self.agent.refused.add(_bare(msg.sender))
                    continue
                if payload.get("event") == EVENT_PARKED:
                    self._remember_parked(payload)
                    continue
                if kind != PERFORMATIVE_PROPOSE:
                    continue
                score = payload.get("content", {}).get("score", 0)
                offers.append((score, msg))
                print(f"[{self.agent.name}] Offer from {_short(msg.sender)}: {score:.0f}")
                self.agent._log(f"offer from {_short(msg.sender)} score {score:.0f}", "offer")
            return offers

        async def _try_accept(self, offer):
            """Send ACCEPT and wait for the curb's verdict. True = we have the slot."""
            reply = offer.make_reply()
            reply.set_metadata("ontology", ONTOLOGY_PARKING)
            reply.set_metadata("performative", PERFORMATIVE_ACCEPT)
            reply.body = json.dumps({
                "size": self.agent.size,
                "is_ev": self.agent.is_ev,
                "category": self.agent.category,
                "declared_time": STAY_SECONDS,
            })
            await self.send(reply)

            msg, payload = await self._wait_reply(lambda m, p: p.get("status") in ("confirmed", "slot_taken"))
            if msg and payload.get("status") == "confirmed":
                self.agent.registered_as = str(msg.to)
                return True
            return False

        async def _take_best(self, offers):
            offers.sort(key=lambda item: item[0], reverse=True)
            for score, offer in offers:
                curb_jid = _bare(offer.sender)
                if await self._try_accept(offer):
                    prefix = "Emergency priority: took" if self.agent.is_emergency else "Took"
                    print(f"[{self.agent.name}] {prefix} {_short(curb_jid)} (score {score:.0f}).")
                    self.agent._log(f"{prefix.lower()} {_short(curb_jid)} (score {score:.0f})", "accept")
                    await self._commute(curb_jid)
                    await self._park(curb_jid)
                    return True
                print(f"[{self.agent.name}] {_short(curb_jid)} was taken in the meantime. Trying the next one.")
                self.agent._log(f"{_short(curb_jid)} taken — trying next", "refuse")
            return False

        async def _wait_reply(self, accept, timeout=3):
            """Wait for a message matching accept(msg, payload); remember PARKED notices on the way."""
            deadline = asyncio.get_running_loop().time() + timeout
            while asyncio.get_running_loop().time() < deadline:
                msg = await self.receive(timeout=1)
                if not msg:
                    continue
                payload = _payload(msg)
                if payload.get("event") == EVENT_PARKED:
                    self._remember_parked(payload)
                if accept(msg, payload):
                    return msg, payload
            return None, {}

        # ---------- auction ----------
        def _remember_parked(self, payload):
            curb = payload.get("curb")
            if curb:
                self.agent.heard[curb] = payload

        async def _ask_parked_car(self):
            if self.agent.is_emergency or self.agent.bid <= 0 or self.agent.bids_sent:
                return
            target = next((c["jid"] for c in self.agent.curbs if c["jid"] in self.agent.refused), None)
            info = self.agent.heard.get(target) if target else None
            if not info:
                return
            owner = info.get("owner") or info.get("car")
            if not owner or owner in self.agent.bids_sent:
                return
            self.agent.bids_sent.add(owner)
            bid = Message(to=str(info.get("car") or owner))
            bid.set_metadata("ontology", ONTOLOGY_PARKING)
            bid.set_metadata("performative", PERFORMATIVE_REQUEST)
            bid.body = json.dumps({
                "action": ACTION_BID,
                "amount": self.agent.bid,
                "wait": self.agent.hold,
                "curb": target,
                "owner": owner,
                "car": _bare(self.agent.jid),
                # the curb needs these when it records the handoff
                "size": self.agent.size,
                "category": self.agent.category,
                "declared_time": STAY_SECONDS,
            })
            await self.send(bid)
            print(f"[{self.agent.name}] Asked {_short(info.get('car') or owner)} to wait "
                  f"{self.agent.hold}s at {_short(target)}. Offered ${self.agent.bid}.")
            self.agent._log(
                f"bid ${self.agent.bid} → {_short(info.get('car') or owner)} at {_short(target)}",
                "auction",
            )
            self.agent._publish(state="waiting", info=f"bid ${self.agent.bid} at {_short(target)}")

        async def _wait_for_spot(self, seconds):
            deadline = asyncio.get_running_loop().time() + seconds
            while asyncio.get_running_loop().time() < deadline:
                msg = await self.receive(timeout=1)
                if not msg:
                    continue
                payload = _payload(msg)
                event = payload.get("event")
                if event == EVENT_PARKED:
                    self._remember_parked(payload)
                    await self._ask_parked_car()
                elif event == EVENT_AUCTION_WIN:
                    print(f"[{self.agent.name}] {_short(payload.get('car'))} will hold the spot "
                          f"for {payload.get('wait')}s.")
                    self.agent._log(
                        f"auction win hold {payload.get('wait')}s by {_short(payload.get('car'))}",
                        "auction",
                    )
                elif event == EVENT_SPOT_READY:
                    print(f"[{self.agent.name}] The spot was held for me.")
                    self.agent._log("SPOT_READY — driving to held curb", "auction")
                    return payload
                elif event == EVENT_AUCTION_REJECT:
                    print(f"[{self.agent.name}] Lost the auction. Someone offered more.")
                    self.agent._log("auction rejected", "auction")
                    return None
            return None

        # ---------- movement ----------
        def _curb_xy(self, curb_jid):
            bare = _bare(curb_jid)
            for curb in self.agent.curbs:
                if curb.get("jid") == bare:
                    return float(curb.get("x", 0)), float(curb.get("y", 0))
            return None

        async def _commute(self, curb_jid):
            coords = self._curb_xy(curb_jid)
            if coords is None:
                print(f"[{self.agent.name}] No coordinates for {curb_jid}; skipping commute.")
                return

            tx, ty = coords
            # Prefer live map position (Pygame may already be animating the car).
            snap = world.snapshot()["cars"].get(_short(self.agent.jid), {})
            sx = float(snap.get("x", self.agent.x))
            sy = float(snap.get("y", self.agent.y))
            self.agent.x, self.agent.y = sx, sy
            distance = ((tx - sx) ** 2 + (ty - sy) ** 2) ** 0.5
            # Match the visual cruise speed used by world_state.advance_motion.
            duration = max(0.4, distance / 14.0)
            heading = math.atan2(ty - sy, tx - sx)

            self.agent.state = "commuting"
            self.agent.target_curb = _bare(curb_jid)
            self.agent._publish(
                state="commuting",
                target_curb=_bare(curb_jid),
                target_x=tx,
                target_y=ty,
                heading=heading,
                speed=14.0,
                info=f"driving to {_short(curb_jid)}",
                clear_deadline=True,
            )
            print(f"[{self.agent.name}] Driving to {_short(curb_jid)}, distance {distance:.1f}, ~{duration:.1f}s.")
            self.agent._log(f"driving to {_short(curb_jid)} ({distance:.1f} units)", "drive")

            # Pygame moves the sprite toward (target_x, target_y); we only wait for arrival.
            await asyncio.sleep(duration)

            self.agent.x, self.agent.y = tx, ty
            self.agent._publish(
                x=tx, y=ty, heading=heading, state="commuting",
                target_x=tx, target_y=ty, info=f"arrived at {_short(curb_jid)}",
            )
            print(f"[{self.agent.name}] {EVENT_ARRIVED} at {_short(curb_jid)}.")
            self.agent._log(f"arrived at {_short(curb_jid)}", "drive")

        # ---------- parking ----------
        async def _announce(self, curb_jid):
            for peer in self.agent.peers:
                note = Message(to=peer)
                note.set_metadata("ontology", ONTOLOGY_PARKING)
                note.set_metadata("performative", PERFORMATIVE_INFORM)
                note.body = json.dumps({
                    "event": EVENT_PARKED,
                    "curb": curb_jid,
                    "car": _bare(self.agent.jid),
                    "owner": self.agent.registered_as,
                })
                await self.send(note)

        async def _park(self, curb_jid, bought=None):
            bids = []
            stay = STAY_SECONDS
            loop = asyncio.get_running_loop()
            self.agent.state = "parked"
            self.agent.target_curb = _bare(curb_jid)
            deadline = loop.time() + stay
            coords = self._curb_xy(curb_jid) or (self.agent.x, self.agent.y)
            self.agent.x, self.agent.y = coords
            info = (
                f"parked at {_short(curb_jid)} (paid ${bought})"
                if bought else f"parked at {_short(curb_jid)} for {stay}s"
            )
            self.agent._publish(
                x=coords[0],
                y=coords[1],
                state="parked",
                target_curb=_bare(curb_jid),
                target_x=coords[0],
                target_y=coords[1],
                park_deadline=time.time() + stay,
                info=info,
            )
            if bought:
                print(f"[{self.agent.name}] Parked at {_short(curb_jid)} after paying ${bought}.")
                self.agent._log(f"PARKED at {_short(curb_jid)} after paying ${bought}", "park")
            else:
                print(f"[{self.agent.name}] Parked at {_short(curb_jid)} for {stay}s.")
                self.agent._log(f"PARKED at {_short(curb_jid)} ({stay}s)", "park")
            next_note = loop.time() + 5
            next_announce = loop.time()
            while loop.time() < deadline:
                if not bought and loop.time() >= next_announce:
                    await self._announce(curb_jid)
                    next_announce += 2
                msg = await self.receive(timeout=1)
                if msg:
                    payload = _payload(msg)
                    if msg.get_metadata("performative") == PERFORMATIVE_REQUEST and payload.get("action") == ACTION_BID:
                        amount = float(payload.get("amount", 0))
                        wait = int(payload.get("wait", 0))
                        bids.append((amount, wait, msg, payload))
                        print(f"[{self.agent.name}] {_short(payload.get('car'))} offers ${amount:.0f} "
                              f"if I wait {wait}s.")
                        self.agent._log(
                            f"received bid ${amount:.0f} from {_short(payload.get('car'))}",
                            "auction",
                        )
                    elif payload.get("event") == EVENT_PARKED:
                        self._remember_parked(payload)
                        await self._ask_parked_car()
                    elif payload.get("event") == EVENT_AUCTION_REJECT:
                        print(f"[{self.agent.name}] Lost the auction. Someone offered more.")
                now = loop.time()
                if now >= next_note and now < deadline:
                    left = int(deadline - now)
                    if left > 0:
                        print(f"[{self.agent.name}] Still parked, {left}s left.")
                        self.agent._publish(info=f"parked at {_short(curb_jid)}, {left}s left")
                    next_note += 5

            if bought or not bids:
                await self._leave(curb_jid)
                return

            bids.sort(key=lambda item: item[0], reverse=True)
            winning_amount, winning_wait, winning_msg, winning_payload = bids[0]
            winner = winning_payload.get("car")
            print(f"[{self.agent.name}] I'll wait {winning_wait}s for {_short(winner)}. "
                  f"${winning_amount:.0f} beats the other offers.")
            self.agent._log(
                f"holds {_short(curb_jid)} for {_short(winner)} (${winning_amount:.0f})",
                "auction",
            )
            for amount, wait, msg, payload in bids:
                answer = msg.make_reply()
                answer.set_metadata("ontology", ONTOLOGY_PARKING)
                answer.set_metadata("performative", PERFORMATIVE_INFORM)
                won = msg == winning_msg
                answer.body = json.dumps({
                    "event": EVENT_AUCTION_WIN if won else EVENT_AUCTION_REJECT,
                    "amount": winning_amount,
                    "wait": winning_wait,
                    "curb": payload.get("curb") or curb_jid,
                    "car": _bare(self.agent.jid),
                })
                await self.send(answer)
            await self._hold_for_winner(curb_jid, winning_wait, winner, winning_payload)

        async def _hold_for_winner(self, curb_jid, seconds, winner, winning_payload):
            self.agent.state = "holding"
            self.agent._publish(
                state="holding",
                target_curb=_bare(curb_jid),
                park_deadline=time.time() + seconds,
                info=f"holding for {_short(winner)} ({seconds}s)",
            )
            # Tell the curb this is a planned, paid extension, so it is not an overstay.
            extend = Message(to=curb_jid)
            extend.set_metadata("ontology", ONTOLOGY_PARKING)
            extend.set_metadata("performative", PERFORMATIVE_INFORM)
            extend.body = json.dumps({
                "event": EVENT_EXTEND,
                "car": _bare(self.agent.jid),
                "extra": seconds,
            })
            await self.send(extend)

            loop = asyncio.get_running_loop()
            deadline = loop.time() + seconds
            next_note = loop.time()
            while loop.time() < deadline:
                left = int(deadline - loop.time())
                if loop.time() >= next_note and left > 0:
                    print(f"[{self.agent.name}] Holding {_short(curb_jid)} for {_short(winner)}, {left}s left.")
                    next_note += 5
                await self.receive(timeout=1)  # the Inbox behaviour handles logging

            ready = Message(to=winner)
            ready.set_metadata("ontology", ONTOLOGY_PARKING)
            ready.set_metadata("performative", PERFORMATIVE_INFORM)
            ready.body = json.dumps({
                "event": EVENT_SPOT_READY,
                "curb": curb_jid,
                "amount": winning_payload.get("amount"),
                "car": _bare(self.agent.jid),
            })
            await self.send(ready)
            await self._handoff(curb_jid, winning_payload)

        async def _handoff(self, curb_jid, winning_payload):
            winner = winning_payload.get("car")
            notice = Message(to=curb_jid)
            notice.set_metadata("ontology", ONTOLOGY_PARKING)
            notice.set_metadata("performative", PERFORMATIVE_REQUEST)
            notice.body = json.dumps({
                "action": ACTION_P2P_HANDOFF,
                "old_owner": winning_payload.get("owner"),
                "new_car": winner,
                "size": winning_payload.get("size", "medium"),
                "category": winning_payload.get("category", "rest"),
                "declared_time": winning_payload.get("declared_time", STAY_SECONDS),
            })
            await self.send(notice)
            print(f"[{self.agent.name}] Spot is free for {_short(winner)}. Curb, please record it.")
            self.agent._log(f"handoff → {_short(winner)} at {_short(curb_jid)}", "auction")
            _, payload = await self._wait_reply(
                lambda m, p: p.get("status") in ("handoff_success", "handoff_failed"))
            if payload:
                print(f"[{self.agent.name}] Curb says: {payload.get('status')}.")
                self.agent._log(f"curb handoff: {payload.get('status')}", "park")
            # Previous owner leaves the bay after selling the wait.
            await self._leave(curb_jid)

        async def _leave(self, curb_jid):
            self.agent.state = "leaving"
            ex, ey = self.agent.x + 8, self.agent.y - 6
            self.agent._publish(
                state="leaving",
                target_x=ex,
                target_y=ey,
                speed=16.0,
                info=f"leaving {_short(curb_jid)}",
                clear_deadline=True,
            )
            print(f"[{self.agent.name}] Time is up. Leaving {_short(curb_jid)}.")
            self.agent._log(f"LEAVE {_short(curb_jid)}", "leave")
            world.toast(f"car {self.agent.name} left the parking")
            leave = Message(to=curb_jid)
            leave.set_metadata("ontology", ONTOLOGY_PARKING)
            leave.set_metadata("performative", PERFORMATIVE_INFORM)
            leave.body = json.dumps({"event": "LEAVE", "car": _bare(self.agent.jid)})
            await self.send(leave)
            _, payload = await self._wait_reply(lambda m, p: p.get("status") in ("departed", "unknown_car"))
            if payload.get("status") == "departed":
                print(f"[{self.agent.name}] Curb recorded the departure.")
                self.agent._log(f"curb recorded departure from {_short(curb_jid)}", "leave")
            await asyncio.sleep(0.8)
            self.agent.state = "done"
            self.agent._publish(state="done", info="done", clear_deadline=True, clear_target=True)