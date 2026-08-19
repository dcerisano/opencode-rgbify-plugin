#!/usr/bin/env python3
"""BLE bridge: stream newline-delimited text from stdin to the RGBify projector.

Interrupt semantics end to end: every line is a new message that supersedes
anything still in flight. The host auralizer and the BLE write path each keep
only the LATEST line — a newer line interrupts (replaces) the previous one at
the next note/chunk boundary, so the last message is the only message. Nothing
is queued, delayed, or replayed.

Discovers the projector at connect time (by advertised service UUID, then name),
chunks each line into codepoint-safe pieces that fit within the projector's TEXT_BRIDGE
payload limit, and writes them to the TEXT_BRIDGE characteristic. Reconnects forever
with backoff so the plugin stays a silent no-op while the projector is out of
range or powered off.

Every line is ALSO auralized on the host (sounddevice) at the firmware's native
cadence (one ~33ms note per char, log-scale freq table identical to the
firmware, whitespace = rest), so
sound never stops while the projector is away. The host auralizer runs always
and independently of the BLE connection; when the projector is reachable both
play the same line (best-effort sync). Lines missed while the projector is down
are dropped for the projector (no replay on reconnect) so the two stay in sync.

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
import time
import threading
import asyncio
import subprocess

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
TEXT_BRIDGE_UUID = "8bc01404-0009-4bf4-95d1-ce27a0477183"
VOLUME_UUID = "8bc01404-0004-4bf4-95d1-ce27a0477183"
DEVICE_NAME = "RGBify Projector"
MAX_BYTES = 200
RECONNECT_DELAY = 2.0
SCAN_TIMEOUT = 5.0
# The firmware shows a " Connect " banner and plays a connect sound on every
# BLE connect. wavAC() blocks the main loop until playback completes, so any
# line written while it plays gets its notes reset by the next chunk and is
# never auralized. Hold queued lines until the connect sound is done: the
# current connect sound is dialup_wav (88000 samples @ 16kHz = 5.5s), so 6s.
CONNECT_SETTLE_MS = 6.0
# The projector advertises as soon as it powers up, but needs a moment to
# finish booting before it can accept a BLE connection. Wait this long after
# discovering it before connecting.
CONNECT_DELAY = 5.0

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


# Mirror the firmware's log-scale auralizer lookup table exactly (firmware
# rgbify-projector-esp32.ino `auralizer_freq[91]`, committed 691c899):
# index = ord(c) - 32 for ASCII 32..122, mapping ' '=space..'z' -> 5000..100 Hz.
AURALIZER_FREQ = [
    5000, 4887, 4778, 4672, 4569, 4468, 4371, 4276,
    4183, 4093, 4004, 3918, 3834, 3752, 3671, 3593,
    3515, 3440, 3366, 3293, 3222, 3153, 3084, 3017,
    2951, 2886, 2823, 2760, 2698, 2638, 2578, 2520,
    2462, 2405, 2349, 2294, 2240, 2187, 2134, 2082,
    2031, 1980, 1931, 1881, 1833, 1785, 1738, 1691,
    1645, 1600, 1555, 1510, 1466, 1423, 1380, 1338,
    1296, 1255, 1214, 1173, 1133, 1094, 1055, 1016,
    978, 940, 902, 865, 828, 792, 756, 720,
    684, 649, 615, 580, 546, 513, 479, 446,
    413, 381, 348, 316, 285, 253, 222, 191,
    161, 130, 100,
]


def synth_note(ch: str, volume: int) -> "np.ndarray":
    """Return int16 mono PCM samples for ONE char (~33ms note).

    Frequency mirrors the firmware auralizer exactly (log-scale lookup table,
    same values as the firmware's `auralizer_freq[c - 32]`); whitespace is a
    rest. Played one note at a time so a newer line can interrupt mid-message.
    """
    n_samples = int(SAMPLE_RATE * NOTE_SEC)
    c = ord(ch)
    if ch in " \t\n\r" or not (32 <= c <= 122):
        return np.zeros(n_samples, dtype=np.int16)
    freq = AURALIZER_FREQ[c - 32]
    peak = (max(0, min(10, volume)) / 10.0) * 32767 * 0.05
    t = np.arange(n_samples)
    samples = np.sin(2.0 * np.pi * freq * t / SAMPLE_RATE)
    return (samples * peak).astype(np.int16)


class HostAuralizer:
    """Always-on host audio sink. Plays one ~33ms note at a time; a newer note
    interrupts (replaces) the previous one, so the last message is the only
    message on the host too."""

    def __init__(self) -> None:
        self._stream = None
        self._pending = None  # int16 mono PCM of the current note
        self._event = threading.Event()
        self._thread = None
        self._stop = False

    def start(self) -> None:
        if not (SOUNDDEVICE_OK and HOST_AURALIZER):
            return
        try:
            self._stream = sd.OutputStream(
                samplerate=SAMPLE_RATE, channels=1, dtype="int16"
            )
            self._stream.start()
        except Exception as e:
            self._stream = None
            print(f"err host auralizer unavailable: {e}", flush=True)
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        print("ok host auralizer", flush=True)

    def _run(self) -> None:
        stream = self._stream
        while not self._stop:
            self._event.wait()
            self._event.clear()
            note = self._pending
            if note is None or stream is None:
                continue
            try:
                stream.write(note)
            except Exception as e:
                print(f"err host auralizer play: {e}", flush=True)

    def play_note(self, note: "np.ndarray") -> None:
        # Latest wins: a note pushed while the previous one is playing replaces
        # it — the pump picks it up as soon as the current write finishes.
        self._pending = note
        self._event.set()

    # Mirror the firmware's volume-change chirp: a short ~1970 Hz beep at the
    # current volume, so the host confirms volume changes like the projector.
    CHIRP_HZ = 1970
    CHIRP_SEC = 0.030

    def chirp(self) -> None:
        if self._stream is None:
            return
        n = int(SAMPLE_RATE * self.CHIRP_SEC)
        t = np.arange(n)
        samples = np.sin(2.0 * np.pi * self.CHIRP_HZ * t / SAMPLE_RATE)
        peak = (max(0, min(10, load_volume())) / 10.0) * 32767 * 0.05
        self.play_note((samples * peak).astype(np.int16))

    def stop(self) -> None:
        self._stop = True
        self._event.set()
        if self._thread is not None:
            self._thread.join(timeout=0.5)
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


async def main() -> None:
    override = os.environ.get("RGBIFY_PROJECTOR_ADDR", "").strip()
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: protocol, sys.stdin)

    lines: asyncio.Queue = asyncio.Queue()
    # Latest-line slots (maxsize 1, replace-on-full): each sink keeps only the
    # most recent line, so a newer line interrupts (replaces) the previous one.
    host_line: asyncio.Queue = asyncio.Queue(maxsize=1)
    ble_line: asyncio.Queue = asyncio.Queue(maxsize=1)

    # While disconnected, no lines reach either sink — nothing is queued or
    # replayed. Delivery starts fresh with the first line after a connect.
    connected = False

    auralizer = HostAuralizer()

    async def read_stdin() -> None:
        while True:
            raw = await reader.readline()
            if not raw:
                # stdin closed = the opencode plugin that spawned us is gone.
                # Exit immediately (and drop the BLE connection) so we never
                # linger as an orphaned process holding the projector.
                os._exit(0)
            await lines.put(raw.decode("utf-8", "replace").rstrip("\n"))

    def push_latest(q: asyncio.Queue, line: str) -> None:
        try:
            q.put_nowait(line)
        except asyncio.QueueFull:
            q.get_nowait()
            q.put_nowait(line)

    async def broadcast() -> None:
        # Fan every line out to both sinks as the LATEST line. If a sink is
        # still busy with an older line, the older one is discarded — the last
        # message is the only message. While disconnected, lines are dropped so
        # nothing accumulates for replay on reconnect.
        while True:
            line = await lines.get()
            if not connected:
                continue
            push_latest(host_line, line)
            push_latest(ble_line, line)

    async def host_auralize() -> None:
        # One ~33ms note per char, in cadence with the firmware (30 fps). A new
        # line replaces the current one at the next note boundary (interrupt).
        auralizer.start()
        current = None
        pos = 0
        try:
            while True:
                try:
                    current = host_line.get_nowait()
                    pos = 0
                except asyncio.QueueEmpty:
                    pass
                if current is not None and pos < len(current):
                    auralizer.play_note(synth_note(current[pos], load_volume()))
                    pos += 1
                else:
                    current = None
                await asyncio.sleep(NOTE_SEC)
        finally:
            auralizer.stop()

    asyncio.create_task(read_stdin())
    asyncio.create_task(broadcast())
    host_task = asyncio.create_task(host_auralize())

    async def ble_loop() -> None:
        nonlocal connected
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
            # The projector advertises immediately on power-up but isn't ready to
            # accept a connection until it finishes booting. Give it a moment.
            await asyncio.sleep(CONNECT_DELAY)
            try:
                # When the projector resets/reboots, drop any queued text so
                # stale lines buffered before the disconnect are not delivered
                # on reconnect.
                def on_disconnect(_client) -> None:
                    nonlocal connected
                    connected = False
                    for q in (host_line, ble_line):
                        try:
                            q.get_nowait()
                        except asyncio.QueueEmpty:
                            pass
                    print("disconnect", flush=True)
                    # Belt-and-suspenders: force-clear BlueZ's connection state
                    # so no stale writes survive into the next connection.
                    try:
                        subprocess.Popen(
                            ["bluetoothctl", "disconnect", addr],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                    except Exception:
                        pass

                async with BleakClient(addr, disconnected_callback=on_disconnect) as client:
                    # Clear anything that slipped in before the flag flipped, so
                    # delivery starts fresh with the first line after connect.
                    for q in (host_line, ble_line):
                        try:
                            q.get_nowait()
                        except asyncio.QueueEmpty:
                            pass
                    connected = True
                    # Acquire the negotiated ATT MTU. Bleak 3.x keeps the
                    # public _acquire_mtu() off the wrapper — it lives on the
                    # backend (_acquire_mtu on client._backend) — so the old
                    # call raised AttributeError and was swallowed, leaving mtu
                    # at 23 and capping every payload at 20 chars. The firmware
                    # raises its local MTU to 517, so we get the full MAX_BYTES.
                    try:
                        await client._backend._acquire_mtu()
                    except Exception:
                        pass
                    mtu = client.mtu_size
                    limit = min(MAX_BYTES, max(1, mtu - 3))
                    print(f"ok {addr}", flush=True)
                    # The firmware plays a connect sound via wavAC() which blocks
                    # the main loop until playback completes. Any line written
                    # while it plays gets its notes reset by the next chunk and is
                    # never auralized. Hold the FIRST line after each connect until
                    # the connect sound is done (dialup_wav = 5.5s), no matter when
                    # it arrives.
                    connected_at = time.monotonic()
                    first_write = True
                    # Read the current projector volume on connect so the host
                    # state file starts in sync, then subscribe to VOLUME
                    # notifications so later changes (e.g. from the RGBify
                    # website) are mirrored live.
                    try:
                        value = await client.read_gatt_char(VOLUME_UUID)
                        if value:
                            save_volume(value[0])
                    except Exception:
                        pass
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
                                ble_line.get(), timeout=IDLE_CHECK_MS
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
                        if first_write:
                            first_write = False
                            elapsed = time.monotonic() - connected_at
                            if elapsed < CONNECT_SETTLE_MS:
                                await asyncio.sleep(CONNECT_SETTLE_MS - elapsed)
                        # Interrupt semantics: deliver the latest line in chunks,
                        # but if a newer line arrives mid-delivery, drop the rest
                        # of this one — the outer loop picks up the new line.
                        for chunk in chunk_text(text, limit):
                            if not ble_line.empty():
                                break
                            try:
                                await client.write_gatt_char(
                                    TEXT_BRIDGE_UUID, chunk.encode("utf-8"), response=True
                                )
                            except Exception:
                                # Transient/failed write: the next line supersedes
                                # this one anyway, so just move on.
                                break
                        print("ok", flush=True)
            except Exception as e:
                connected = False
                print(f"err {e}", flush=True)
                await asyncio.sleep(RECONNECT_DELAY)

    await asyncio.gather(host_task, ble_loop())


if __name__ == "__main__":
    asyncio.run(main())
