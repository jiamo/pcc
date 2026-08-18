import asyncio

from pproxy import proto


def test_http_guess_waits_for_fragmented_connect_prefix() -> None:
    async def run() -> None:
        reader = asyncio.StreamReader()
        reader.feed_data(b"C")

        async def feed_tail() -> None:
            await asyncio.sleep(0)
            reader.feed_data(b"ONNECT example.com:443 HTTP/1.1\r\n\r\n")

        tail = asyncio.create_task(feed_tail())
        matched = await proto.HTTP(None).guess(reader)
        await tail

        assert matched is True
        assert await reader.readexactly(4) == b"CONN"

    asyncio.run(run())


def test_http_guess_does_not_wait_for_four_byte_socks5_greeting() -> None:
    async def run() -> None:
        reader = asyncio.StreamReader()
        reader.feed_data(b"\x05\x01\x00")

        matched = await asyncio.wait_for(proto.HTTP(None).guess(reader), timeout=0.1)

        assert not matched
        assert await reader.readexactly(3) == b"\x05\x01\x00"

    asyncio.run(run())
