import json
from spade.agent import Agent
from spade.behaviour import CyclicBehaviour
from spade.message import Message
from ontology import *

class EnforcementAgent(Agent):
    """
    Agent responsible for enforcing parking rules.
    Listens for overstay reports from CurbAgents and issues fines directly to cars.
    """

    class PatrolBehaviour(CyclicBehaviour):
        """Behaviour to continuously listen for overstay reports from curbs."""
        
        async def run(self):
            # Wait for incoming messages
            msg = await self.receive(timeout=10)
            if msg:
                performative = msg.get_metadata("performative")
                content = json.loads(msg.body) if msg.body else {}

                # Check if the message is a request from a CurbAgent to issue a fine
                if performative == PERFORMATIVE_REQUEST and content.get("action") == "issue_fine":
                    offending_car = content.get("car_id")
                    curb_id = str(msg.sender)
                    
                    print(f"[ENFORCER] Received overstay report from {curb_id} for car {offending_car}.")
                    
                    fine_amount = 50.0
                    if offending_car not in self.agent.fines_registry:
                        self.agent.fines_registry[offending_car] = 0
                    self.agent.fines_registry[offending_car] += fine_amount
                    
                    print(f"[ENFORCER] Issuing ${fine_amount} fine to {offending_car}. Total debt: ${self.agent.fines_registry[offending_car]}")

                    # Send fine notification directly to the CarAgent
                    fine_msg = Message(to=offending_car)
                    fine_msg.set_metadata("performative", PERFORMATIVE_INFORM)
                    fine_msg.body = json.dumps({
                        "action": "fine_issued",
                        "amount": fine_amount,
                        "reason": f"Overstay at {curb_id}",
                        "total_debt": self.agent.fines_registry[offending_car]
                    })
                    
                    await self.send(fine_msg)
                    print(f"[ENFORCER] Fine sent to {offending_car}.")

    async def setup(self):
        print(f"[ENFORCER] {self.jid} is online and patrolling the streets.")
        
        self.fines_registry = {}

        patrol_behaviour = self.PatrolBehaviour()
        self.add_behaviour(patrol_behaviour)