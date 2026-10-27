# kører en bully_node som HTTP-server i hver pod, modtager beskeder på /message
# node.tick() kaldes hvert 100 ms, /crash og /recover bliver brugt til at simulere nedbrud i eksperimenter

import asyncio
import os
import socket
import time
from typing import Optional

from aiohttp import ClientSession, ClientTimeout, web

from bully_node import BullyNode, Message, MsgType
from improved_bully_node import ImprovedBullyNode

# valg af algoritme, enten "classic" eller "improved"
ALGORITHMS = {"classic": BullyNode, "improved": ImprovedBullyNode}
ALGORITHM = os.environ.get("ALGORITHM", "classic")
NodeClass = ALGORITHMS[ALGORITHM]

# konfiguration med miljøvariabler ud fra statefulset.yaml
SERVICE = os.environ.get("SERVICE_NAME", "bully")
CLUSTER_SIZE = int(os.environ.get("CLUSTER_SIZE", "5"))
PORT = int(os.environ.get("PORT", "8080"))
STARTUP_DELAY = float(os.environ.get("STARTUP_DELAY", "3"))
TICK_INTERVAL = 0.1
# adresse på andre noder, muligt at overwrite ved lokale tests
PEER_URL = os.environ.get("PEER_URL_TEMPLATE", "http://{service}-{id}.{service}:{port}")


def get_node_id() -> int:
    # til lokal test
    if "NODE_ID" in os.environ:
        return int(os.environ["NODE_ID"])
    # finder frem til id ud fra podnavn, "bully-3" -> id: 3
    return int(socket.gethostname().rsplit("-", 1)[1])


NODE_ID = get_node_id()


def log(text: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {text}", flush=True)


def peer_url(node_id: int) -> str:
    return PEER_URL.format(service=SERVICE, id=node_id, port=PORT)


# udgående beskeder
async def post_message(app: web.Application, target: int, msg: Message) -> None:
    try:
        async with app["session"].post(f"{peer_url(target)}/message",
                                       json=msg.to_dict()) as resp:
            await resp.read()
    except Exception as e:
        if msg.type != MsgType.HEARTBEAT:
            log(f"Node {NODE_ID}: kunne ikke nå node {target} med {msg.type.value} "
                f"({type(e).__name__})")


class Runtime:
    # podens tilstand samlet i et objekt, /recover kan udskifte noden under eksperimentet

    def __init__(self, app: web.Application):
        self.crashed = False
        # gemmer tasks, så de ikke slettes, før HTTP-kaldet er færdigt
        self.tasks: set[asyncio.Task] = set()
        self.node: BullyNode = self.make_node(app)
        # tidsmåling til eksperimenter, holder styr på tidspunkter for ændringer, fx ny coordinator osv.
        self.coordinator_since: Optional[float] = None
        self.first_election_at: Optional[float] = None
        self._seen_coordinator: Optional[int] = None
        self._seen_elections = 0

    def make_node(self, app: web.Application) -> BullyNode:
        return NodeClass(NODE_ID, range(CLUSTER_SIZE), send=make_send(app), log=log)

    # tjekker om noden har fået ny coordinator eller startet et valg, og gemmer tidspunktet
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
        # ny node med opstartstilstand, bevarer beskedtallet fra før crashet, så det tæller med i eksperimentet
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
    # send-funktion til BullyNode. HTTP-kald startes i baggrunden og returnerer med det samme
    def send(target: int, msg: Message) -> None:
        rt: Runtime = app["rt"]
        # død node sender ingenting
        if rt.crashed:
            return
        task = asyncio.create_task(post_message(app, target, msg))
        rt.tasks.add(task)
        task.add_done_callback(rt.tasks.discard)
    return send


# HTTP-endpoints
async def handle_message(request: web.Request) -> web.Response:
    rt: Runtime = request.app["rt"]
    # død node afviser beskeder, så afsenderen ser den er død
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
    # tvinger noden til at starte en election
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
    # starter noden på ny og lader den starte en election
    rt: Runtime = request.app["rt"]
    if rt.crashed:
        rt.recover(request.app)
        log(f"Node {NODE_ID}: *** RECOVER ***")
        rt.node.start()
        rt.observe()
    return web.json_response({"ok": True})


# opstart og tick()
async def tick_loop(rt: Runtime) -> None:
    # delay giver de andre noder tid til at starte op
    await asyncio.sleep(STARTUP_DELAY)
    if not rt.crashed:
        rt.node.start()
        rt.observe()
    while True:
        if not rt.crashed:
            # rt.node hentes hver gang, så operationer kører på den nye node efter /recover
            rt.node.tick()
            rt.observe()
        await asyncio.sleep(TICK_INTERVAL)


async def lifecycle(app: web.Application):
    # besked regnes som fejlet, hvis modtageren ikke svarer inden for 1 sek
    app["session"] = ClientSession(timeout=ClientTimeout(total=1))
    app["rt"] = rt = Runtime(app)
    loop_task = asyncio.create_task(tick_loop(rt))
    # serveren kører her, koden efter yield kører ved nedlukning
    yield
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
