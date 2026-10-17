"""
app.py — runner der pakker BullyNode ind i en HTTP-server (aiohttp).

Hver pod koerer denne fil. Den goer tre ting:
  1. Modtager beskeder fra andre pods paa  POST /message  og giver dem til node.on_message()
  2. Kalder node.tick() hvert 100 ms (timeouts og heartbeats)
  3. Sender beskeder til andre pods som HTTP POST (send-funktionen BullyNode faar) 

ID og adresser:
  En StatefulSet giver poddene navnene bully-0, bully-1, ... Tallet bruges som ID.
  Den headless service giver hver pod et fast DNS-navn: bully-3.bully -> pod 3's IP.
  Derfor kan vi naa node k paa  http://bully-k.bully:8080  uden at kende dens IP.

Alt koerer i én asyncio event loop, saa der er ingen traade og ingen locks.

Fejlinjektion (til eksperimenter og systemtest):
  POST /crash    noden opfoerer sig som doed: ignorerer beskeder, sender intet, tikker ikke.
                 HTTP-serveren koerer videre, saa /status og /recover stadig virker.
  POST /recover  noden genstarter med frisk tilstand (som efter et rigtigt crash) og
                 starter en election — Garcia-Molinas recovery-tilfaelde.
"""

import asyncio
import os
import socket
import time
from typing import Optional

from aiohttp import ClientSession, ClientTimeout, web

from bully_node import BullyNode, Message, MsgType
from improved_bully_node import ImprovedBullyNode

# Vælg algoritme på baggrund af "classic" og "improved"
ALGORITHMS = {"classic": BullyNode, "improved": ImprovedBullyNode}
ALGORITHM = os.environ.get("ALGORITHM", "classic")
NodeClass = ALGORITHMS[ALGORITHM]

# ----------------------------------------------------------------------
# Konfiguration (saettes som miljoevariabler i statefulset.yaml)
# ----------------------------------------------------------------------
SERVICE = os.environ.get("SERVICE_NAME", "bully")
CLUSTER_SIZE = int(os.environ.get("CLUSTER_SIZE", "5"))
PORT = int(os.environ.get("PORT", "8080"))
STARTUP_DELAY = float(os.environ.get("STARTUP_DELAY", "3"))
TICK_INTERVAL = 0.1
# Adresse paa node {id}. Kan overskrives til lokal test uden Kubernetes.
PEER_URL = os.environ.get("PEER_URL_TEMPLATE", "http://{service}-{id}.{service}:{port}")


def get_node_id() -> int:
    if "NODE_ID" in os.environ:                       # til lokal test
        return int(os.environ["NODE_ID"])
    return int(socket.gethostname().rsplit("-", 1)[1])  # "bully-3" -> 3


NODE_ID = get_node_id()


def log(text: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {text}", flush=True)


def peer_url(node_id: int) -> str:
    return PEER_URL.format(service=SERVICE, id=node_id, port=PORT)


# ----------------------------------------------------------------------
# Udgaaende beskeder
# ----------------------------------------------------------------------
async def post_message(app: web.Application, target: int, msg: Message) -> None:
    try:
        async with app["session"].post(f"{peer_url(target)}/message",
                                       json=msg.to_dict()) as resp:
            await resp.read()
    except Exception as e:
        if msg.type != MsgType.HEARTBEAT:
            log(f"Node {NODE_ID}: kunne ikke naa node {target} med {msg.type.value} "
                f"({type(e).__name__})")


class Runtime:
    """Den foranderlige tilstand for denne pod. Ligger i ét objekt, fordi aiohttp ikke
    vil have, at man saetter app[...] efter opstart (/recover udskifter noden)."""

    def __init__(self, app: web.Application):
        self.crashed = False
        self.tasks: set[asyncio.Task] = set()  # hold en reference, saa tasks ikke garbage-collectes
        self.node: BullyNode = self.make_node(app)
        # Tidsmaaling til eksperimenter. Algoritmen kender intet til dem: runneren
        # kigger paa noden efter hvert kald (observe) og noterer, hvornaar noget aendrer sig.
        self.coordinator_since: Optional[float] = None  # vaeg-tid for sidste lederskift
        self.first_election_at: Optional[float] = None  # foerste election siden /stats/reset
        self._seen_coordinator: Optional[int] = None
        self._seen_elections = 0

    def make_node(self, app: web.Application) -> BullyNode:
        return NodeClass(NODE_ID, range(CLUSTER_SIZE), send=make_send(app), log=log)

    def observe(self) -> None:
        now = time.time()
        if self.node.coordinator_id != self._seen_coordinator:
            self._seen_coordinator = self.node.coordinator_id
            if self._seen_coordinator is not None:
                self.coordinator_since = now
        if self.node.stats.elections_started > self._seen_elections and self.first_election_at is None:
            self.first_election_at = now
        self._seen_elections = self.node.stats.elections_started

    def recover(self, app: web.Application) -> None:
        """Ny node med frisk tilstand, men samme beskedtal (de taeller med i eksperimentet)."""
        stats = self.node.stats
        self.node = self.make_node(app)
        self.node.stats = stats
        self._seen_coordinator = None
        self.crashed = False

    def reset_stats(self) -> None:
        self.node.stats.reset()
        self.first_election_at = None
        self._seen_elections = 0


def make_send(app: web.Application):
    """Send-funktionen BullyNode faar. Den starter et HTTP-kald i baggrunden og
    returnerer med det samme — ligesom UDP blokerer den aldrig algoritmen."""
    def send(target: int, msg: Message) -> None:
        rt: Runtime = app["rt"]
        if rt.crashed:                            # en doed node sender intet
            return
        task = asyncio.create_task(post_message(app, target, msg))
        rt.tasks.add(task)
        task.add_done_callback(rt.tasks.discard)
    return send


# ----------------------------------------------------------------------
# HTTP-endpoints
# ----------------------------------------------------------------------
async def handle_message(request: web.Request) -> web.Response:
    rt: Runtime = request.app["rt"]
    if rt.crashed:
        return web.json_response({"ok": False, "crashed": True}, status=503)
    msg = Message.from_dict(await request.json())
    rt.node.on_message(msg)
    rt.observe()
    return web.json_response({"ok": True})


async def handle_status(request: web.Request) -> web.Response:
    rt: Runtime = request.app["rt"]
    node = rt.node
    return web.json_response({
        "id": node.id,
        "algorithm": ALGORITHM,
        "crashed": rt.crashed,
        "state": node.state.value,
        "coordinator": node.coordinator_id,
        "coordinator_since": rt.coordinator_since,
        "first_election_at": rt.first_election_at,
        "elections_started": node.stats.elections_started,
        "election_messages_sent": node.stats.election_sent(),
        "sent": {t.value: n for t, n in node.stats.sent.items()},
        "received": {t.value: n for t, n in node.stats.received.items()},
    })


async def handle_reset_stats(request: web.Request) -> web.Response:
    request.app["rt"].reset_stats()
    return web.json_response({"ok": True})


async def handle_start_election(request: web.Request) -> web.Response:
    """Tving denne node til at starte en election (bruges i eksperimenter)."""
    rt: Runtime = request.app["rt"]
    if not rt.crashed:
        rt.node.start_election()
        rt.observe()
    return web.json_response({"ok": True})


async def handle_crash(request: web.Request) -> web.Response:
    request.app["rt"].crashed = True
    log(f"Node {NODE_ID}: *** CRASH (simuleret) ***")
    return web.json_response({"ok": True})


async def handle_recover(request: web.Request) -> web.Response:
    """Genstart noden med frisk tilstand og lad den starte en election."""
    rt: Runtime = request.app["rt"]
    if rt.crashed:
        rt.recover(request.app)
        log(f"Node {NODE_ID}: *** RECOVER ***")
        rt.node.start()
        rt.observe()
    return web.json_response({"ok": True})


# ----------------------------------------------------------------------
# Livscyklus
# ----------------------------------------------------------------------
async def tick_loop(rt: Runtime) -> None:
    await asyncio.sleep(STARTUP_DELAY)   # giv de andre pods tid til at komme op
    if not rt.crashed:
        rt.node.start()
        rt.observe()
    while True:
        if not rt.crashed:
            rt.node.tick()                 # slaa op hver gang: /recover udskifter noden
            rt.observe()
        await asyncio.sleep(TICK_INTERVAL)


async def lifecycle(app: web.Application):
    app["session"] = ClientSession(timeout=ClientTimeout(total=1))
    app["rt"] = rt = Runtime(app)
    loop_task = asyncio.create_task(tick_loop(rt))
    yield                                 # her koerer serveren
    loop_task.cancel()
    await app["session"].close()


def main() -> None:
    app = web.Application()
    app.add_routes([
        web.post("/message", handle_message),
        web.get("/status", handle_status),
        web.post("/stats/reset", handle_reset_stats),
        web.post("/election", handle_start_election),
        web.post("/crash", handle_crash),
        web.post("/recover", handle_recover),
    ])
    app.cleanup_ctx.append(lifecycle)
    log(f"Node {NODE_ID} starter ({ALGORITHM}, cluster size {CLUSTER_SIZE}, port {PORT})")
    web.run_app(app, port=PORT, print=None)


if __name__ == "__main__":
    main()
