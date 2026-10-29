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

## 5b. Measured on a TSX (listen-only, 2026-10-29)

- TER port at **9600 baud**: polls decode cleanly; 19200 / 4800 give only garbage.
- Parity not measurable by listening (Windows does not report parity errors);
  odd assumed, to be confirmed by the first exchange.
- The master polls link addresses **1 to 8**, in order, `DLE ENQ <addr>`.
- With no slave answering: about 3 polls per second, i.e. the master waits ~0.3 s
  per silent address. A slave therefore has at least that long to start answering.
- PL7 closed: no address answers, so the PC can use address 1 (like PL7).
- Still unknown: what a slave sends when polled with nothing to say (EOT expected).

## 5c. First exchange (2026-10-29, TSX 3721, no valid application)

- Mirror `FA` with 10 bytes: exact echo. **Confirmed:** 9600 8O1, PC = slave 1,
  category 0, X-WAY header `20 00 FE 00 00 00`, frame and BCC rules.
- Identification `0F`: `FF 04 65 08 "TSX 3721" 08 05 24 30 11 02 00 00`
  (length-prefixed product name; other bytes to decode).
- Status `31`: negative answer `FD` (not supported on TSX, or refused without application).

## 5d. Probe of read requests (TSX 3721, no application)

- Accepted: **`01` (V1) at address 0 → `31 50`**: one data byte, no "forced" byte.
  Probably system bits %S0..%S7 (%S4 and %S6 = clock bits). To confirm with `ut_sysbits.py`.
- Refused (`FD`): V1 `00`, `02`..`07`; `36` with segments 0x64..0x6B × types 05..08.
  `00` reads %M on a TSX (x-way-uni-te), so refusals are likely due to the missing
  application (no %M/%MW memory). Re-run the probe once the application is reloaded.

## 5e. System bits confirmed (TSX 3721, no application)

- **`01` = read system bit, confirmed.** Request `01 <cat> <bit addr lo> <hi>`, answer
  `31 <byte>` = the 8 bits of the group containing the address (address 1 → same byte as 0,
  address 8 → %S8..%S15).
- %S5 and %S6 change between reads (clock bits): live data. %S4 read 1 seven times
  (not explained yet), %S7 stayed 1 (1-min clock, consistent over ~1 min).
- %S8 = 1, all of %S9..%S127 = 0, including %S10 (I/O fault when 0) and the battery bits.
  Without an application these may simply not be maintained: not usable for diagnosis
  until the application is reloaded.

## 5f. Second TSX, with an application (STOP, RUN flashing, I/O LED on, ERR off)

- %S4, %S5, %S6 all change between reads: every clock bit is live (on the first PLC,
  without application, %S4 looked frozen).
- **%S10 = 0 (I/O fault) matches the I/O LED.** First cross-check of a %S label against
  the PLC's own LEDs.
- %S16 = 1 (no I/O fault in the task: likely not updated in STOP), %S21 = 1,
  %S30 = 1 (master task active, to confirm), %S51 = 1 (real-time clock time lost), %S8 = 1.
- Battery bits %S66..%S68 = 0, BAT LED off: consistent.

## 5g. Probe with an application (second TSX)

Accepted at address 0 (all values 0):
- V1 `00` → `30 <2 bytes>` (%M: forced byte + value byte), `01` → `31 <byte>` (%S),
  `04`, `05`, `06` → `3x <word>` (three word areas, which is which to confirm).
- `36` segment `68`: word (07) and double word (08) → probably %MW / %MD.
- `36` segment `69`: word and double word → probably %KW / %KD.
- `36` segment `6A`: word only → probably %SW.
- Answer: `66 <type> <data>`. Still refused: types 05 (bit) and 06 (byte), segments 64..67, 6B.

Next: `ut_words.py` reads wider ranges and the real-time clock (%SW49..53) to confirm the
segment mapping, the byte order and which V1 code reads which area.

## 5h. Segment mapping confirmed from the PLC itself (second TSX)

- **Segment 6A = %SW, words little-endian:** %SW50 (seconds, `SS00` in BCD) read
  0300 → 0400 → 0700 over ~3 s; %SW51 = 0521 (05:21), %SW52 = 0121 (21 Jan),
  %SW49 = 1 (day of week). %SW53 = A001 is not valid BCD (clock lost, %S51 = 1).
- %SW11 = 250 = watchdog time in ms (TSX default). %SW13 = 254 = 0xFE = the station
  number we address in the X-WAY header. %SW15 = 6504 = bytes `04 65`, the same as in
  the identification answer.
- Other non-zero: %SW12 = 6, %SW18 = AAA8 (likely a running counter), %SW32 = FFFF,
  %SW67 = 00D0, %SW68 = 0F00, %SW80 = %SW81 = 7A0E, %SW97 = FFFF, %SW98 = 010B, %SW99 = 010A.
- Segments 68 and 69: words 0..119 all 0 (assumed %MW / %KW, not confirmed by a value yet).
- **V1 `06` reads %SW** (address 11 → 00FA, same as segment 6A). `04` and `05` read 0 there,
  consistent with %MW / %KW.
- Fault words (to read next): %SW124 CPU fault type, %SW125 blocking fault type,
  %SW126/127 blocking fault address (TSX Micro, from memory).
- %SW120..%SW127 all 0, consistent with no blocking fault (ERR off; I/O LED only).

## 5i. tsx.py first run on the bench

- Read (%MW10 via segment 68) worked. Write `37` and RUN `24` both stopped at
  "waiting for ACK": the PLC never acknowledged the frame. Those runs answered polls on
  addresses 1-3, so the request may have gone out on address 2 or 3.
- Default set back to address 1 only; the tool now resends when polled again without ACK.
- **Then confirmed with `--trace` (address 1):**
  - **Write objects works:** `37 00 68 07 0A 00 01 00 D2 04` (%MW10 := 1234) → `FE`,
    read back `66 07 D2 04`. Segment 68 = %MW confirmed with a non-zero value.
  - **RUN works:** `24 00` → `FE`; the PLC went to RUN with the empty rack (I/O fault is
    not blocking).
  - Real cause of the failures: polls piled up in the PC's receive buffer while the user
    typed OUI; the tool answered stale polls and talked out of turn (frame sent 2-3 times).
    Fixed: receive buffer and parser are cleared at the start of every request.
  - Address 2-3 mode is probably fine after this fix; to retest.

## 5j. Rack configuration: what is feasible

- **Physical rack (what is plugged in):** identification `0F` sent to each module address
  through the X-WAY header (network, station, gate, ext1, ext2). Addressing to find:
  `ut_rack.py` tries gates 0..15 on module 0, then ext1 = 1..5 and ext2 = 1..5 on gate 0.
- **Configured rack (what the application expects):** the application creates status objects
  for each configured module, even when the module is absent. Reading them slot by slot
  would show "configured but absent". Object codes unknown; needs an I/O module to find.
- **Uploading the application** to decode its configuration: proprietary binary, not planned.
- **Writing the configuration:** means downloading a modified application. Not planned:
  stays in PL7.

## 5k. Rack probe, round 1 (TSX 3721, empty rack)

Identification `0F` with header `20 00 FE <gate> <ext1> <ext2>`:
- Gate 0: CPU answers, **ext1/ext2 ignored** (any value → TSX 3721). Gate 0 does not route to slots.
- Gate 8, ext 0/0: CPU answers too.
- Gate 1: answers `0F 00` (neither refusal nor identification; other service).
- Refused (`22`), refusal layout `22 <net> <station> <gate> <ext1> <ext2> FF <reason>`:
  gates 4, 6, 7, 9..15 → reason `0D`; **gate 5 → reason `07`**; gates 2 and 3 come back
  rewritten as gate 5, ext1 FE, ext2 2 / 3, reason `0D`.
  → gate 5 is probably the module/channel gate, needing ext1/ext2.
- Identification differs from the first PLC: `08 03 22 30` here vs `08 05 24 30` (firmware?).

Round 2: gates 5 and 8 with ext1 0..5, ext2 0..3.

## 5l. Rack probe, round 2 (gates 5 and 8, ext1 0..5, ext2 0..3)

- **Gate 8 = module addressing:** ext1 0 / ext2 0 → CPU (TSX 3721).
- Gate 8, **ext1 4 / ext2 0 → refused, reason `07`**; every other ext1 / ext2 → reason `0D`.
- Gate 5: ext 0/0 → `07`, everything else `0D`.
- Working hypothesis: `0D` = no such address, `07` = address exists but cannot answer.
  Slot 4 would then be configured by the application but empty (→ the I/O fault), or 4 is
  the first valid slot number on a TSX 37-21. Next: gate 8, ext1 0..31, ext2 0; then a
  module in a slot to confirm the numbering.

## 5m. Rack probe, round 3 (gate 8, ext1 0..31, ext2 0)

- ext1 0 → CPU `TSX 3721`; **ext1 16 (0x10) → CPU again**;
  **ext1 20 (0x14) → `TSX P37-21 Prise Terminal`** (the terminal port: a CPU sub-part
  answering identification on its own). ext1 4 → reason `07`; all others → `0D`.
- 0/16 and 4/20 differ by bit 0x10, so ext1 4 is most likely the terminal port addressed
  without that flag. **The round-2 hypothesis "slot 4 configured but empty" is withdrawn.**
- Identification layout: `FF 04 <type> <name length> <name> <version...>`
  (type 65 = CPU, 10 = terminal port).
- Next: ext1 32..127 (other built-in parts, module positions), ext2 0..7 on ext1 16 and 20,
  and a module in a slot to anchor the numbering.

## 5n. Rack probe, round 4 (empty rack)

- ext1 16 and 20 with ext2 1..7: all refused `0D`. ext2 is not used for these two parts.
- ext1 32..127, ext2 0: nothing answers.
- So with an empty rack, gate 8 shows exactly two parts: the CPU (ext1 0 / 16) and the
  terminal port (ext1 20). Consistent with the rack being empty.
- Slot numbering still unknown: needs one module in the rack.

## 5o. Modules added: TSX DEZ 32D2 (32 digital inputs) + TSX AEZ 414 (4 analog inputs)

- Gate 8, ext1 0..63: still only CPU (0 / 16) and terminal port (20). Simple I/O modules
  have no communication server, so they do not answer identification.
- Their %I / %IW and status words need the I/O object request codes, still unknown.
- Plan: capture PL7 talking to the PLC through `ut_spy.py` (relay between a com0com
  virtual port and the real port, decodes every request and answer).

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
