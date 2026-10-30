#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
TSX Serial Proxy / Sniffer - low-latency version
Bridges PL7 Pro (via com0com virtual port) to the real PLC port,
logging all traffic in hex for protocol analysis.

Setup:
  1. Install com0com -> creates pair e.g. COM10 <-> COM11
  2. Configure PL7 Pro Uni-Telway driver to use COM10
  3. Run this script: python tsx_serial_proxy.py COM11 COM1
     (COM11 = other end of virtual pair, COM1 = real cable to PLC)

Works with Python 2.7 + pyserial 2.7 on Windows XP (and Python 3).

What keeps the relay fast:
  - the relay threads only read and write: no flush(), no console, no lock;
    each chunk is handed to a logging thread through a queue
  - process priority set to HIGH, relay threads to TIME_CRITICAL (Windows)
  - the console shows a counter, not every byte (the XP console is slow)
If the real port is a USB-serial adapter (FTDI), also set its latency
timer to 1 ms (Device Manager > port > Advanced).

During the capture, type a note + Enter (e.g. "upload"): it goes into the log.
"""
from __future__ import print_function

import os
import sys
import threading
import time

import serial

try:
    import Queue as queue  # Python 2
except ImportError:
    import queue  # Python 3

LOG_FILE = "tsx_capture.log"

# precise clock: time.clock on Python 2 / Windows, perf_counter on Python 3
clock = getattr(time, "perf_counter", None) or time.clock

log_queue = queue.Queue()
counts = {"PL7->PLC": 0, "PLC->PL7": 0}


def boost_priority():
    """Windows only: raise process and current thread priority. Ignored elsewhere."""
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        k32.SetPriorityClass(k32.GetCurrentProcess(), 0x00000080)  # HIGH_PRIORITY_CLASS
        k32.SetThreadPriority(k32.GetCurrentThread(), 15)          # THREAD_PRIORITY_TIME_CRITICAL
    except Exception:
        pass


def forward(src, dst, direction, stop_event):
    """Read from src and write to dst at once. Logging happens elsewhere."""
    boost_priority()
    while not stop_event.is_set():
        try:
            data = src.read(1)
            if not data:
                continue
            n = src.inWaiting()
            if n:
                data += src.read(n)
            dst.write(data)                      # no flush(): it waits for the bytes to leave
            log_queue.put((clock(), time.time(), direction, data))
        except serial.SerialException as e:
            log_queue.put((clock(), time.time(), "ERROR", ("%s: %s" % (direction, e)).encode()))
            stop_event.set()
            break


def writer(log_fh, t0, stop_event):
    """Logging thread: formats and writes everything the relay threads queued."""
    while not (stop_event.is_set() and log_queue.empty()):
        try:
            tc, tw, direction, data = log_queue.get(timeout=0.2)
        except queue.Empty:
            continue
        stamp = time.strftime("%H:%M:%S", time.localtime(tw))
        if direction in counts:
            counts[direction] += len(data)
            hex_str = " ".join("%02X" % b for b in bytearray(data))
            line = "[%s %10.3f ms] %s [%3d] %s\n" % (stamp, (tc - t0) * 1000, direction, len(data), hex_str)
        else:
            line = "[%s %10.3f ms] ##### %s %s\n" % (stamp, (tc - t0) * 1000, direction,
                                                    data.decode("ascii", "replace"))
        log_fh.write(line)
        log_fh.flush()


def notes(stop_event):
    read = raw_input if sys.version_info[0] == 2 else input  # noqa: F821
    while not stop_event.is_set():
        try:
            text = read()
        except (EOFError, KeyboardInterrupt):
            return
        if text.strip():
            log_queue.put((clock(), time.time(), "NOTE", text.strip().encode("ascii", "replace")))
            print("  note: %s" % text.strip())


def open_port(name, baud):
    # PL7 Pro uses ODD parity on the terminal port (seen in the Portmon log and on the bench)
    return serial.Serial(port=name, baudrate=baud, bytesize=serial.EIGHTBITS,
                         parity=serial.PARITY_ODD, stopbits=serial.STOPBITS_ONE, timeout=0.05)


def main():
    if len(sys.argv) < 3:
        print("Usage: python tsx_serial_proxy.py <PL7_PORT> <PLC_PORT> [baud]")
        print("Example: python tsx_serial_proxy.py COM11 COM1 9600")
        sys.exit(1)

    pl7_port, plc_port = sys.argv[1], sys.argv[2]
    baud = int(sys.argv[3]) if len(sys.argv) > 3 else 9600

    print("=" * 60)
    print("TSX Serial Proxy / Sniffer (low latency)")
    print("PL7 side : %s   PLC side : %s   %d 8O1" % (pl7_port, plc_port, baud))
    print("Log file : %s" % os.path.abspath(LOG_FILE))
    print("=" * 60)

    try:
        ser_pl7 = open_port(pl7_port, baud)
        ser_plc = open_port(plc_port, baud)
    except Exception as e:
        print("ERROR opening ports: %s" % e)
        sys.exit(1)
    ser_pl7.flushInput()
    ser_plc.flushInput()

    log_fh = open(LOG_FILE, "a")
    log_fh.write("\n=== New capture session %s ===\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
    log_fh.write("PL7=%s  PLC=%s  Baud=%d  Parity=ODD\n\n" % (pl7_port, plc_port, baud))

    stop_event = threading.Event()
    t0 = clock()
    threads = [
        threading.Thread(target=forward, args=(ser_pl7, ser_plc, "PL7->PLC", stop_event)),
        threading.Thread(target=forward, args=(ser_plc, ser_pl7, "PLC->PL7", stop_event)),
        threading.Thread(target=writer, args=(log_fh, t0, stop_event)),
        threading.Thread(target=notes, args=(stop_event,)),
    ]
    for t in threads:
        t.daemon = True
        t.start()

    print("Proxy running. Start PL7 and connect. Type a note + Enter to mark the log.")
    print("Ctrl+C to stop.\n")
    try:
        while not stop_event.is_set():
            time.sleep(2)
            sys.stdout.write("\r  PL7->PLC %7d bytes   PLC->PL7 %7d bytes   " %
                             (counts["PL7->PLC"], counts["PLC->PL7"]))
            sys.stdout.flush()
    except KeyboardInterrupt:
        print("\n\nStopping...")
    stop_event.set()
    threads[2].join(2)
    ser_pl7.close()
    ser_plc.close()
    log_fh.close()
    print("Log saved to: %s" % os.path.abspath(LOG_FILE))


if __name__ == "__main__":
    main()
