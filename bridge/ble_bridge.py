#!/usr/bin/env python3
"""BLE bridge: stream newline-delimited text from stdin to the RGBify projector.

Discovers the projector at connect time (by advertised service UUID, then name),
chunks each line into codepoint-safe pieces that fit within the projector's TEXT
payload limit, and writes them to the TEXT characteristic. Reconnects forever
with backoff so the plugin stays a silent no-op while the projector is out of
range or powered off.

Every line is ALSO auralized on the host (sounddevice) at the firmware's native
cadence (one ~33ms note per char, freq = -1021 + c*37 Hz, whitespace = rest), so
sound never stops while the projector is away. The host auralizer runs always
and independently of the BLE connection; when the projector is reachable both
play the same line (line-level best-effort sync). Lines missed while the
projector is down are dropped for the projector (no replay on reconnect) so the
two stay in sync.

Set RGBIFY_PROJECTOR_ADDR to skip discovery and use a fixed address.
Set RGBIFY_HOST_AURALIZER=0 to disable the host auralizer (projector unaffected).

Host volume is mirrored from the projector's VOLUME characteristic whenever the
projector is connected and persisted to the state file below so it survives
restarts. While the projector is down you can still adjust the host volume by
editing that file (or set RGBIFY_VOLUME as an initial default).

stdout protocol (one line per event, for debugging):
  ok <addr>          connected / a line delivered
  err <message>      delivery failure (will retry)
"""
import os
import sys
import threading
import asyncio

from bleak import BleakClient, BleakScanner

try:
    import sounddevice as sd
    import numpy as np
    SOUNDDEVICE_OK = True
except ImportError:
    sd = None
    np = None
    SOUNDDEVICE_OK = False

SERVICE_UUID = "8bc01404-0000-4bf4-95d1-ce27a0477183"
TEXT_UUID = "8bc01404-0007-4bf4-95d1-ce27a0477183"
VOLUME_UUID = "8bc01404-0004-4bf4-95d1-ce27a0477183"
DEVICE_NAME = "RGBify Projector"
MAX_BYTES = 200
RECONNECT_DELAY = 2.0
SCAN_TIMEOUT = 5.0
# The firmware shows a " Connect " banner and plays a connect sound on every
# BLE connect. If the first line is written immediately after connect it gets
# masked by that banner/sound. Hold queued lines this long after connect.
CONNECT_SETTLE_MS = 2.0

# Host auralizer: mirrors the firmware Auralizer (one note per frame @ 30fps,
# freq = -1021 + c*37 Hz for non-space chars, whitespace = rest, volume 0-10).
SAMPLE_RATE = 44100
NOTE_SEC = 1.0 / 30
HOST_AURALIZER = os.environ.get("RGBIFY_HOST_AURALIZER", "1").strip() != "0"
HOST_VOLUME = int(os.environ.get("RGBIFY_VOLUME", "10").strip() or "10")
# Persisted host volume: mirrored from the projector's VOLUME characteristic
# when connected; editable at any time even when the projector is off. Lives in
# the global opencode config dir (the user may not be inside a project), with
# RGBIFY_STATE_DIR as an explicit override.
STATE_DIR = os.environ.get(
    "RGBIFY_STATE_DIR",
    os.path.join(os.path.expanduser("~"), ".config", "opencode", "state"),
)
VOLUME_FILE = os.path.join(STATE_DIR, "host-volume")


def load_volume() -> int:
    try:
        with open(VOLUME_FILE) as f:
            return max(0, min(10, int(f.read().strip())))
    except (OSError, ValueError):
        return HOST_VOLUME


def save_volume(value: int) -> None:
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(VOLUME_FILE, "w") as f:
            f.write(str(max(0, min(10, value))))
    except OSError:
        pass


def synth_text(text: str, volume: int) -> "np.ndarray":
    """Return int16 mono PCM samples for one line, one ~33ms note per char.

    Frequency mirrors the firmware auralizer (`-1021 + c*37` Hz); whitespace is
    a rest. Notes are continuous (no gaps between chars) like the firmware's
    pitch glide.
    """
    n_samples = int(SAMPLE_RATE * NOTE_SEC)
    frames = []
    peak = (max(0, min(10, volume)) / 10.0) * 32767 * 0.05
    for ch in text:
        freq = -1021 + ord(ch) * 37
        t = n_samples
        if ch in " \t\n\r" or freq <= 0:
            frames.append(np.zeros(t, dtype=np.int16))
            continue
        samples = np.sin(2.0 * np.pi * freq * np.arange(t) / SAMPLE_RATE)
        frames.append((samples * peak).astype(np.int16))
    if not frames:
        return np.zeros(0, dtype=np.int16)
    return np.concatenate(frames)


class HostAuralizer:
    """Always-on host audio sink fed from the broadcast loop."""

    def __init__(self) -> None:
        self._stream = None
        self._lock = threading.Lock()

    def start(self) -> None:
        if not (SOUNDDEVICE_OK and HOST_AURALIZER):
            return
        try:
            self._stream = sd.OutputStream(
                samplerate=SAMPLE_RATE, channels=1, dtype="int16"
            )
            self._stream.start()
            print("ok host auralizer", flush=True)
        except Exception as e:
            self._stream = None
            print(f"err host auralizer unavailable: {e}", flush=True)

    def play(self, text: str) -> None:
        if self._stream is None:
            return
        with self._lock:
            try:
                self._stream.write(synth_text(text, load_volume()))
            except Exception as e:
                print(f"err host auralizer play: {e}", flush=True)

    # Mirror the firmware's volume-change chirp: a short ~1970 Hz beep at the
    # current volume, so the host confirms volume changes like the projector.
    CHIRP_HZ = 1970
    CHIRP_SEC = 0.030

    def chirp(self) -> None:
        if self._stream is None:
            return
        volume = load_volume()
        n = int(SAMPLE_RATE * self.CHIRP_SEC)
        t = np.arange(n)
        samples = np.sin(2.0 * np.pi * self.CHIRP_HZ * t / SAMPLE_RATE)
        peak = (max(0, min(10, volume)) / 10.0) * 32767 * 0.05
        with self._lock:
            try:
                self._stream.write((samples * peak).astype(np.int16))
            except Exception as e:
                print(f"err host auralizer chirp: {e}", flush=True)

    def stop(self) -> None:
        with self._lock:
            if self._stream is not None:
                try:
                    self._stream.stop()
                except Exception:
                    pass
                self._stream = None


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
WRITE_RETRIES = 10
WRITE_RETRY_DELAY = 1.0


async def main() -> None:
    override = os.environ.get("RGBIFY_PROJECTOR_ADDR", "").strip()
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: protocol, sys.stdin)

    lines: asyncio.Queue = asyncio.Queue()
    host_q: asyncio.Queue = asyncio.Queue()
    ble_q: asyncio.Queue = asyncio.Queue()

    auralizer = HostAuralizer()

    async def read_stdin() -> None:
        while True:
            raw = await reader.readline()
            if not raw:
                await lines.put(None)
                return
            await lines.put(raw.decode("utf-8", "replace").rstrip("\n"))

    async def broadcast() -> None:
        # Fan every line out to both sinks simultaneously. The host queue is
        # awaited (never drops); the BLE queue drops-on-full so a slow/down
        # projector can never stall the always-on host auralizer. Lines lost
        # here were already auralized on the host and are skipped on reconnect.
        while True:
            line = await lines.get()
            await host_q.put(line)
            try:
                ble_q.put_nowait(line)
            except asyncio.QueueFull:
                pass

    async def host_auralize() -> None:
        auralizer.start()
        try:
            while True:
                line = await host_q.get()
                if line is None:
                    return
                if line:
                    await asyncio.to_thread(auralizer.play, line)
        finally:
            auralizer.stop()

    asyncio.create_task(read_stdin())
    asyncio.create_task(broadcast())
    host_task = asyncio.create_task(host_auralize())

    async def ble_loop() -> None:
        while True:
            try:
                addr = await discover_address(override)
            except Exception as e:
                print(f"err scan failed: {e}", flush=True)
                await asyncio.sleep(RECONNECT_DELAY)
                continue
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
                    # Let the firmware finish its " Connect " banner/sound before
                    # delivering queued lines so the first prompt isn't masked.
                    if CONNECT_SETTLE_MS > 0 and not ble_q.empty():
                        await asyncio.sleep(CONNECT_SETTLE_MS)
                    # Warm up the ATT write path after every connect. The first
                    # write following a fresh link can be silently dropped by the
                    # ESP32 (it races the connection-parameter update). Read the
                    # current VOLUME and write it back unchanged: a no-op ping that
                    # primes the path and chirps the piezo (volume setter), without
                    # touching TEXT or changing any state.
                    try:
                        value = await client.read_gatt_char(VOLUME_UUID)
                        await client.write_gatt_char(VOLUME_UUID, value, response=True)
                        # Mirror the projector volume into the host state file so
                        # both auralizers share the same volume setting.
                        if value:
                            save_volume(value[0])
                    except Exception:
                        pass
                    # Subscribe to VOLUME notifications so a volume change made
                    # elsewhere (e.g. the RGBify website) is mirrored to the
                    # host state file live, even while connected.
                    def on_volume_changed(_handle, data: bytes) -> None:
                        if data:
                            save_volume(data[0])
                            auralizer.chirp()

                    try:
                        await client.start_notify(VOLUME_UUID, on_volume_changed)
                    except Exception:
                        pass
                    while True:
                        try:
                            text = await asyncio.wait_for(
                                ble_q.get(), timeout=IDLE_CHECK_MS
                            )
                        except asyncio.TimeoutError:
                            if not client.is_connected:
                                raise ConnectionError("projector disconnected while idle")
                            continue
                        if text is None:
                            return
                        if not text:
                            print("ok", flush=True)
                            continue
                        # The first write right after a fresh connect is commonly
                        # flaky. Retry on the SAME connection with backoff instead of
                        # tearing down and rescanning (which is slow and can drop the
                        # line). Only give up if the connection itself is lost, then
                        # requeue the line and reconnect so it isn't lost.
                        delivered = False
                        while not delivered:
                            for attempt in range(1, WRITE_RETRIES + 1):
                                try:
                                    for chunk in chunk_text(text, limit):
                                        await client.write_gatt_char(
                                            TEXT_UUID, chunk.encode("utf-8"), response=True
                                        )
                                    delivered = True
                                    break
                                except Exception:
                                    if not client.is_connected:
                                        break
                                    if attempt < WRITE_RETRIES:
                                        await asyncio.sleep(WRITE_RETRY_DELAY)
                            if delivered:
                                break
                            await ble_q.put(text)
                            raise ConnectionError("connection lost during write")
                        print("ok", flush=True)
            except Exception as e:
                print(f"err {e}", flush=True)
                await asyncio.sleep(RECONNECT_DELAY)

    await asyncio.gather(host_task, ble_loop())


if __name__ == "__main__":
    asyncio.run(main())
