"""Product-shaped local HTTP/1 path compiled by the current pcc1 gate.

This source intentionally exercises the pcc-owned request codec, router,
framework dispatch and response encoder without claiming live socket, TLS,
proxy or epoll transport coverage.
"""

from pcc.gateway.lifecycle import GatewayLifecycle
from pcc.gateway.server import GatewayConfig, GatewayConnection
import pcc.virtual_thread as virtual_thread
from pcc.web import App, BodyStream, Request, Response, get, post


def health(request: Request):
    return Response.text("pcc1-local-health")


def echo(request: Request):
    return Response.bytes(request.read_body())


def incremental_reader(request: Request):
    return request.read_body()


def incremental_body_probe() -> int:
    body = BodyStream(32, 2, 4, True)
    request = Request("POST", "/incremental", body=body, content_length=4)
    child = virtual_thread.spawn(incremental_reader, request)
    # Let the single reader reach Event.wait before the connection-owner model
    # feeds the first fragment.  feed/finish wake it; join is the only result
    # owner and close releases any retained views exactly once.
    virtual_thread.yield_now()
    virtual_thread.call(body.feed, b"pc")
    virtual_thread.yield_now()
    virtual_thread.call(body.feed, b"c1")
    virtual_thread.call(body.finish)
    result = virtual_thread.join(child)
    virtual_thread.call(body.close)
    if result != b"pcc1":
        return 1
    if body.consumed_size() != 4:
        return 2
    return 0


def gateway_probe() -> int:
    if incremental_body_probe() != 0:
        return 8
    app = App(routes=(get("/health", health), post("/echo", echo)))
    config = GatewayConfig()
    lifecycle = GatewayLifecycle(config, config.admission)
    lifecycle.start()
    lifecycle.started()
    if not lifecycle.admit_connection():
        return 1
    generation = lifecycle.acquire_generation()
    connection = GatewayConnection(
        app, -1, None, lifecycle, generation, config
    )
    if virtual_thread.call(connection.feed_data, b"GET /hea") != 0:
        return 2
    if virtual_thread.call(
        connection.feed_data,
        b"lth HTTP/1.1\r\nHost: local\r\n\r\n"
        b"POST /echo HTTP/1.1\r\nHost: local\r\nContent-Length: 4\r\n\r\npcc1",
    ) != 5:
        return 3
    output = connection.take_output()
    if b"HTTP/1.1 200 OK" not in output:
        return 4
    if b"pcc1-local-health" not in output or not output.endswith(b"pcc1"):
        return 5
    if lifecycle.metrics.get("requests_started") != 2:
        return 6
    if lifecycle.metrics.get("requests_active") != 0:
        return 7
    connection.close("acceptance-complete")
    generation.release()
    lifecycle.release_connection()
    print("PCC1_GATEWAY_HTTP1_LOCAL_OK")
    return 0


def main() -> int:
    thread = virtual_thread.spawn(gateway_probe)
    virtual_thread.run(1, 128)
    return virtual_thread.result(thread)


main()
