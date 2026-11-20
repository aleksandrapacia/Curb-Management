# FIPA-ACL Performatives used in the system

PERFORMATIVE_CFP = "cfp"               # Car asks for parking
PERFORMATIVE_PROPOSE = "propose"       # Curb offers a spot
PERFORMATIVE_REFUSE = "refuse"         # Curb denies access
PERFORMATIVE_ACCEPT = "accept-proposal"  # Car accepts the offered spot
PERFORMATIVE_INFORM = "inform"         # General info
PERFORMATIVE_REQUEST = "request"       # Enforcer requests fine payment

# Priority mapping
PRIORITIES = {
    "emergency": 1,
    "disabled": 2,
    "shipping": 3,
    "big_family": 4,
    "rest": 5
}

# What a waiting car pays the parked car to keep the spot until it arrives.
# Emergency does not pay anyone to wait.
BIDS = {
    "emergency": 0,
    "disabled": 150,
    "shipping": 100,
    "big_family": 80,
    "rest": 200
}

# How many seconds the parked car is asked to hold the spot.
HOLD_SECONDS = {
    "emergency": 0,
    "disabled": 10,
    "shipping": 5,
    "big_family": 10,
    "rest": 15
}

# Cars say these to each other. The curb only records the final handoff.
ACTION_BID = "bid"
ACTION_P2P_HANDOFF = "p2p_handoff"
EVENT_PARKED = "PARKED"
EVENT_AUCTION_WIN = "AUCTION_WIN"
EVENT_AUCTION_REJECT = "AUCTION_REJECT"
EVENT_SPOT_READY = "SPOT_READY"
EVENT_COMMUTING = "COMMUTING"
EVENT_ARRIVED = "ARRIVED"

# Car movement toward a curb (units per second). Used for viz / commute delay.
COMMUTE_SPEED = 8.0
COMMUTE_STEP_SECONDS = 0.05

# Vehicle size mapping
SIZE_SPACES = {
    "small": 1,
    "medium": 1,
    "large": 2
}

# General constants
ONTOLOGY_PARKING = "curbside_parking_ontology"

# Parking time declared by cars, and how many extra seconds the curb tolerates
# (commute time + small margin) before it reports an overstay.
STAY_SECONDS = 20
OVERSTAY_GRACE = 10

# Small score bonus for an EV at a curb that has EV spots.
EV_BONUS = 50

# Parked car tells the curb it will stay longer because it sold the spot's waiting time.
EVENT_EXTEND = "EXTEND"