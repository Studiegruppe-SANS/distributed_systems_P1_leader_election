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
"""

import asyncio
import os
import socket
import time

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


def make_send(app: web.Application):
    """Send-funktionen BullyNode faar. Den starter et HTTP-kald i baggrunden og
    returnerer med det samme — ligesom UDP blokerer den aldrig algoritmen."""
    def send(target: int, msg: Message) -> None:
        task = asyncio.create_task(post_message(app, target, msg))
        app["tasks"].add(task)                    # hold en reference, saa tasken
        task.add_done_callback(app["tasks"].discard)  # ikke garbage-collectes
    return send


# ----------------------------------------------------------------------
# HTTP-endpoints
# ----------------------------------------------------------------------
async def handle_message(request: web.Request) -> web.Response:
    msg = Message.from_dict(await request.json())
    request.app["node"].on_message(msg)
    return web.json_response({"ok": True})


async def handle_status(request: web.Request) -> web.Response:
    node: BullyNode = request.app["node"]
    return web.json_response({
        "id": node.id,
        "state": node.state.value,
        "coordinator": node.coordinator_id,
        "elections_started": node.stats.elections_started,
        "election_messages_sent": node.stats.election_sent(),
        "sent": {t.value: n for t, n in node.stats.sent.items()},
        "received": {t.value: n for t, n in node.stats.received.items()},
    })


async def handle_reset_stats(request: web.Request) -> web.Response:
    request.app["node"].stats.reset()
    return web.json_response({"ok": True})


async def handle_start_election(request: web.Request) -> web.Response:
    """Tving denne node til at starte en election (bruges i eksperimenter)."""
    request.app["node"].start_election()
    return web.json_response({"ok": True})


# ----------------------------------------------------------------------
# Livscyklus
# ----------------------------------------------------------------------
async def tick_loop(app: web.Application) -> None:
    await asyncio.sleep(STARTUP_DELAY)   # giv de andre pods tid til at komme op
    node: BullyNode = app["node"]
    node.start()
    while True:
        node.tick()
        await asyncio.sleep(TICK_INTERVAL)


async def lifecycle(app: web.Application):
    app["session"] = ClientSession(timeout=ClientTimeout(total=1))
    app["tasks"] = set()
    app["node"] = NodeClass(NODE_ID, range(CLUSTER_SIZE), send=make_send(app), log=log)
    loop_task = asyncio.create_task(tick_loop(app))
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
    ])
    app.cleanup_ctx.append(lifecycle)
    log(f"Node {NODE_ID} starter ({ALGORITHM}, cluster size {CLUSTER_SIZE}, port {PORT})")
    web.run_app(app, port=PORT, print=None)


if __name__ == "__main__":
    main()
