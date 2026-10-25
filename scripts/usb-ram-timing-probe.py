#!/usr/bin/env python3
"""Per-phase timing diagnostic for the fuxi UEFI USB Bulk RAM receiver.

Imports the production sender module untouched and wraps its transport so
every bulk transfer is timestamped. Sends a bounded prefix (default 256 MiB)
of one research-image window as DATA chunks, then ABORTs the object so no
receiver session state is left behind. HELLO's advertised chunk size is
recorded. Never retries a started transfer; this protocol has no
partition-write operation.

Aggregate model per chunk: wall = payload-OUT + header-OUT + reply-IN +
gaps (host turnaround + receiver processing between transfers).
"""
import argparse
import importlib.util
import json
import struct
import sys
import time
from pathlib import Path

spec = importlib.util.spec_from_file_location('usb_sender', Path(__file__).with_name('usb-ram-send.py'))
sender = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sender)
MIB = 1024 * 1024


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--research-image', type=Path, required=True)
    p.add_argument('--kind', choices=['os', 'systemos'], default='os')
    p.add_argument('--mib', type=int, default=256)
    p.add_argument('--wait-seconds', type=float, default=30)
    p.add_argument('--report', type=Path)
    a = p.parse_args()
    if not 1 <= a.mib <= 8192:
        p.error('--mib out of range')

    stream, size, digest, obj = sender.source(argparse.Namespace(
        self_test=False, file=None, research_image=a.research_image, kind=a.kind))
    records = []

    deadline = time.monotonic() + a.wait_seconds
    while True:
        try:
            transport = sender.UsbTransport()
            break
        except RuntimeError as error:
            if ('not found or inaccessible' not in str(error) or
                    time.monotonic() >= deadline):
                raise
            time.sleep(0.5)
    original = transport.transfer

    def timed(endpoint, data_or_size):
        incoming = bool(endpoint & 0x80)
        nbytes = data_or_size if incoming else len(data_or_size)
        started = time.perf_counter()
        result = original(endpoint, data_or_size)
        records.append((endpoint, nbytes, time.perf_counter() - started))
        return result

    transport.transfer = timed
    wall_start = time.perf_counter()
    try:
        client = sender.Client(transport)
        maximum, _ = client.command(sender.HELLO)
        client.command(sender.BEGIN, obj, payload=struct.pack('<Q', size) + digest)
        sent = 0
        limit = min(a.mib * MIB, size)
        while sent < limit:
            block = stream.read(min(maximum, limit - sent))
            if not block:
                raise RuntimeError('source became shorter during probe')
            client.command(sender.DATA, obj, sent, block)
            sent += len(block)
        client.command(sender.ABORT, obj)
    finally:
        wall = time.perf_counter() - wall_start
        transport.close()
        stream.close()

    payload_out = [r for r in records if r[0] == 1 and r[1] > 4096]
    header_out = [r for r in records if r[0] == 1 and r[1] <= 4096]
    reply_in = [r for r in records if r[0] == 0x81]

    def total(rows):
        return sum(r[2] for r in rows)

    report = dict(
        advertised_chunk=maximum, kind=a.kind, probe_bytes=len(payload_out) * maximum,
        wall_seconds=round(wall, 3),
        payload_transfers=len(payload_out),
        payload_seconds=round(total(payload_out), 3),
        payload_mbytes_per_s=round(sum(r[1] for r in payload_out) / max(total(payload_out), 1e-9) / 1e6, 2),
        header_seconds=round(total(header_out), 3),
        reply_seconds=round(total(reply_in), 3),
        gap_seconds=round(wall - total(payload_out) - total(header_out) - total(reply_in), 3),
        payload_seconds_p50=round(sorted(r[2] for r in payload_out)[len(payload_out) // 2] * 1000, 3),
        aboted_clean=True,
        partition_writes=False)
    if a.report:
        a.report.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, ValueError, OSError) as error:
        raise SystemExit(str(error))
