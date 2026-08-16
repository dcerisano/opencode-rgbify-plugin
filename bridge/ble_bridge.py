#!/usr/bin/env python3
"""BLE bridge: stream newline-delimited text from stdin to the RGBify projector.

Reads UTF-8 text lines from stdin, chunks each line into codepoint-safe pieces
that fit within the projector's TEXT payload limit, and writes them to the TEXT
characteristic. Reconnects forever with backoff so the plugin can stay a silent
no-op while the projector is out of range or powered off.

stdout protocol (one line per event, for debugging):
  ok                 connected / a line delivered
  err <message>      delivery failure (will retry)
"""
import os
import sys
import asyncio

from bleak import BleakClient

TEXT_UUID = "8bc01404-0007-4bf4-95d1-ce27a0477183"
DEFAULT_ADDR = "40:91:51:AB:50:CE"
MAX_BYTES = 200
RECONNECT_DELAY = 2.0


def chunk_text(text: str, limit: int):
    chunks = []
    cur = ""
    cur_bytes = 0
    for ch in text:
        b = len(ch.encode("utf-8"))
        if cur and cur_bytes + b > limit:
            chunks.append(cur)
            cur = ""
            cur_bytes = 0
        cur += ch
        cur_bytes += b
    if cur:
        chunks.append(cur)
    return chunks


async def main() -> None:
    addr = os.environ.get("RGBIFY_PROJECTOR_ADDR", DEFAULT_ADDR)
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: protocol, sys.stdin)

    while True:
        try:
            async with BleakClient(addr) as client:
                limit = min(MAX_BYTES, max(1, client.mtu_size - 3))
                print("ok", flush=True)
                while True:
                    raw = await reader.readline()
                    if not raw:
                        return
                    text = raw.decode("utf-8", "replace").rstrip("\n")
                    if not text:
                        print("ok", flush=True)
                        continue
                    for chunk in chunk_text(text, limit):
                        await client.write_gatt_char(
                            TEXT_UUID, chunk.encode("utf-8"), response=True
                        )
                    print("ok", flush=True)
        except Exception as e:
            print(f"err {e}", flush=True)
            await asyncio.sleep(RECONNECT_DELAY)


if __name__ == "__main__":
    asyncio.run(main())
