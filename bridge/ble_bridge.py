#!/usr/bin/env python3
"""BLE bridge: stream newline-delimited text from stdin to the RGBify projector.

Discovers the projector at connect time (by advertised service UUID, then name),
chunks each line into codepoint-safe pieces that fit within the projector's TEXT
payload limit, and writes them to the TEXT characteristic. Reconnects forever
with backoff so the plugin stays a silent no-op while the projector is out of
range or powered off.

Set RGBIFY_PROJECTOR_ADDR to skip discovery and use a fixed address.

stdout protocol (one line per event, for debugging):
  ok <addr>          connected / a line delivered
  err <message>      delivery failure (will retry)
"""
import os
import sys
import asyncio

from bleak import BleakClient, BleakScanner

SERVICE_UUID = "8bc01404-0000-4bf4-95d1-ce27a0477183"
TEXT_UUID = "8bc01404-0007-4bf4-95d1-ce27a0477183"
DEVICE_NAME = "RGBify Projector"
MAX_BYTES = 200
RECONNECT_DELAY = 2.0
SCAN_TIMEOUT = 5.0


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


async def discover_address(override: str):
    if override:
        return override
    devices = await BleakScanner.discover(timeout=SCAN_TIMEOUT, return_adv=True)
    for addr, (dev, adv) in devices.items():
        if adv and SERVICE_UUID in {u.lower() for u in adv.service_uuids}:
            return addr
        if dev.name == DEVICE_NAME:
            return addr
    return None


IDLE_CHECK_MS = 5.0


async def main() -> None:
    override = os.environ.get("RGBIFY_PROJECTOR_ADDR", "").strip()
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: protocol, sys.stdin)

    queue: asyncio.Queue = asyncio.Queue()

    async def read_stdin() -> None:
        while True:
            raw = await reader.readline()
            if not raw:
                await queue.put(None)
                return
            await queue.put(raw.decode("utf-8", "replace").rstrip("\n"))

    asyncio.create_task(read_stdin())

    while True:
        addr = await discover_address(override)
        if addr is None:
            print("err projector not found", flush=True)
            await asyncio.sleep(RECONNECT_DELAY)
            continue
        try:
            async with BleakClient(addr) as client:
                mtu = 23
                try:
                    await client._acquire_mtu()
                    mtu = client.mtu_size
                except Exception:
                    pass
                limit = min(MAX_BYTES, max(1, mtu - 3))
                print(f"ok {addr}", flush=True)
                while True:
                    try:
                        text = await asyncio.wait_for(queue.get(), timeout=IDLE_CHECK_MS)
                    except asyncio.TimeoutError:
                        if not client.is_connected:
                            raise ConnectionError("projector disconnected while idle")
                        continue
                    if text is None:
                        return
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
