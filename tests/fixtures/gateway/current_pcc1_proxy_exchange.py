"""Current-pcc1 proxy protocol/core acceptance source.

This proves the bounded streaming state machine in a self/no-libpython artifact.
It intentionally does not claim that the live outbound socket bridge is wired.
"""

from pcc.gateway.proxy import RetryPolicy
from pcc.gateway.proxy_http1 import ProxyExchange


def main() -> int:
    exchange = ProxyExchange(
        "POST",
        "/api/items",
        [("host", "front"), ("connection", "x-drop"), ("x-drop", "no")],
        "127.0.0.1:9000",
        "192.0.2.8",
        "http",
        "front",
        content_length=-1,
        chunked_request=True,
        segment_bytes=8,
        low_watermark=8,
        high_watermark=16,
        max_buffered_bytes=1024,
    )
    request_head, resumed = exchange.take_upstream()
    if not resumed:
        return 1
    if b"host: 127.0.0.1:9000\r\n" not in request_head:
        return 2
    if b"x-drop" in request_head:
        return 3
    exchange.feed_request_body(b"pcc")
    exchange.feed_request_body(b"1")
    exchange.finish_request()
    request_body, _ = exchange.take_upstream()
    if request_body != b"3\r\npcc\r\n1\r\n1\r\n0\r\n\r\n":
        return 4

    exchange.feed_upstream(
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n"
        b"Connection: x-hop\r\nX-Hop: remove\r\nX-Keep: yes\r\n\r\n"
        b"4\r\ndo"
    )
    if not exchange.response_committed or exchange.response_finished:
        return 5
    if exchange.can_retry(RetryPolicy(attempts=2), 1, "reset-before-head"):
        return 6
    exchange.feed_upstream(b"ne\r\n0\r\n\r\n")
    response, resumed = exchange.take_downstream()
    if not resumed:
        return 7
    if b"x-hop" in response.lower():
        return 8
    if not response.endswith(b"4\r\ndone\r\n0\r\n\r\n"):
        return 9
    if not exchange.response_finished or not exchange.upstream_keep_alive:
        return 10
    print("PCC1_GATEWAY_PROXY_CORE_OK")
    return 0


main()
