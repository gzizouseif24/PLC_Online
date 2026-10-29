# Uni-Telway (TSX Micro / Premium, TER port) — research notes

Goal: read %MW, %M, %I, %Q from a TSX through the TER port with the serial
TSX PCX 1031 cable, like PL7 Pro does, without PL7.

Status: research only, no driver yet. **Confirmed** = seen in frames captured from
real hardware, or checked here against them. **To verify** = from memory or partial
sources, must be confirmed by the Schneider manual or a capture of PL7 traffic.

Sources:
- Schneider *Uni-Telway Bus Reference Manual* 35000789 (the spec; not readable from this
  environment, to obtain).
- [pyunitelway](https://github.com/Pahegi/pyunitelway) (Python, GPLv3, NUM 1060 CNC):
  framing and frames captured on real hardware. Used to understand the protocol only;
  its code is not copied here (GPL).
- [x-way-uni-te](https://github.com/Elektordi/x-way-uni-te) (Java, MIT, TSX over XIP/Ethernet):
  legacy UNI-TE bit requests.
- Kepware / PTC Uni-Telway driver manuals: 5-level addressing (network, station, gate,
  module, channel), slave IDs.

## 1. The big difference with the other brands

On the TER port **the PLC is the bus master**. The PC is a **slave** with a link
address. The PC cannot just send a request: it must wait until the PLC polls its
address, then send. This is the opposite of MEWTOCOL, Host Link and Modbus.

```
PLC → PC   DLE ENQ <addr>                       poll: "do you have something to send?"
PC  → PLC  EOT                                   nothing to send          (to verify: EOT = 0x04)
   or PC → PLC  DLE STX <addr> <len> <data> <BCC>   our request
PLC → PC   ACK                                   frame received (NAK = rejected)
   ... PLC executes the request ...
PLC → PC   DLE STX <addr> <len> <data> <BCC>     the answer, addressed to us
PC  → PLC  ACK
```

The PC has to answer each poll within a few milliseconds, so the reading loop must be
tight. Real serial port (as preferred) is better than USB for that.

## 2. Frame (confirmed)

```
DLE STX <link addr> <length> <data ...> <BCC>
10  02
```
- `length` = number of data bytes, **before** DLE doubling, 1..134.
- Every `0x10` (DLE) in `length` or `data` is sent twice; the copy is not counted in `length`.
- `BCC` = sum of all bytes sent (header included, after doubling) modulo 256.
  Checked against 3 captured frames.
- Control bytes: DLE 0x10, STX 0x02, ENQ 0x05, ACK 0x06, NAK 0x15.

Captured example (mirror test answer, link address 1):
```
10 02 01 08 | 20 00 FE 00 00 00 | FB 00 | 34
            | X-WAY header      | UNI-TE| BCC
```

## 3. X-WAY header inside the frame

Requests and answers seen on hardware start with `20 00 FE 00 00 00`:
`0x20` (standard message), then the 5-level address of the other end:
network 0, station 0xFE, gate 0, module 0, channel 0.

Station 0xFE / gate 0 is the usual X-WAY "local station, system server", i.e. the PLC
we are plugged into. **To verify on a TSX** (seen on a NUM CNC, matches the
Kepware 5-level addressing and the PL7 address notation `0.254.0`).

Answer header `0x22` = message refused by X-WAY.

## 4. UNI-TE requests

Request: `<request code> <category> <parameters>`. Answer code = request code + 0x30,
except listed ones; `0xFD` = negative answer.

| Request | Code | Answer | Status |
|---|---|---|---|
| Mirror (link test, echo) | 0xFA | 0xFB | confirmed (NUM) |
| Identification | 0x0F | 0x3F | confirmed (NUM) |
| Status | 0x31 | 0x61 | confirmed (NUM) |
| Read objects | 0x36 | 0x66 | confirmed (NUM); TSX object codes to verify |
| Write objects | 0x37 | 0xFE | not needed for reading |
| Read internal bit %M (legacy V1) | 0x00 | 0x30 | Java code, TSX over Ethernet |
| Read internal word %MW (legacy V1) | 0x04 | 0x34 | to verify |

Read objects, as used on hardware:
```
36 <category> <segment> <type> <addr lo> <addr hi> <count lo> <count hi>
answer: 66 <type> <data, little-endian>
```

TSX segment / type values (**all to verify**): the Schneider ETZ manual shows
`16#0564` for %M in a SEND_REQ (type 05 = bit, segment 0x64 = internal bits).
By the same pattern %MW would be type 07 (word), segment 0x68. %I / %Q codes unknown.

Legacy V1 bit read answer: 2 bytes, forced-bits byte then value byte, bit = address mod 8.

## 5. TSX TER port settings (to verify)

- Default: Uni-Telway master, 9600 baud, 8 data bits, odd parity, 1 stop bit.
- PL7 talks as slave link address 1 (and the next ones). The PC tool should use the
  same address PL7 uses, with PL7 closed.
- TSX PCX 1031: RS-232 ↔ RS-485 converter with a selector; keep the position used for PL7.

## 6. How to fill the "to verify" gaps

1. **Schneider manuals:** Uni-Telway reference 35000789 and the UNI-TE / communication
   requests manual for TSX Micro/Premium (object segment and type tables).
2. **Capture PL7 Pro talking to the PLC** with a serial port monitor on the PC while PL7
   animates %MW, %M, %I and %Q in an animation table. Every request PL7 sends shows
   the exact segment/type codes for this PLC. This is the most reliable source: same
   PLC, same cable.
3. Then, on the PLC: listen only (which addresses are polled), mirror test, identification,
   then read %MW known from PL7.

## 7. Driver plan

1. Listener: open the port, print the polls (`DLE ENQ addr`) without answering. Proves
   cable, speed, parity and which link addresses the PLC polls. Nothing is sent.
2. Framing layer: build/parse frames, DLE doubling, BCC, answer polls with EOT.
   Unit-tested with the captured frames.
3. Mirror test (`FA`), like the Omron `TS` echo.
4. Read %MW, %M, then %I / %Q once the codes are confirmed.
