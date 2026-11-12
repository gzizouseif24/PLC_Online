# Mitsubishi FX1N: programming port, I/O, special devices

Sources: FX1N hardware manual JY992D89301 (rev. Q), FX programming manual II JY992D88101 (rev. E),
address map of the FX-232AW manual BT5096-C (tables 1 to 7, as transcribed in the node-fxplc tests).

## Link

SC-09 / USB-SC09 cable on the programming port (mini-DIN 8 under the cover, RS-422), 9600 bauds 7E1.
GX Developer / GX Works must be closed. Frame: `STX cmd payload ETX sum`, sum = low byte of cmd..ETX in
2 hex digits. Commands: `0` read (address 4 hex + count 2 hex, max 64 bytes), `1` write, `7` / `8` force a bit
ON / OFF (bit address, low byte first). ENQ -> ACK when present. Verified against the two documented
example frames (read D123: sum 74; write D123..D124: sum 49).

Readable through this map: X / Y 0..177 (octal), M0..M1023, M8000..M8255, S0..S999, T / C 0..255,
D0..D511, D8000..D8255. **D512..D7999 and M1024..M1535 are not in the documented map**: not readable yet.

## I/O (hardware manual table 1.1, 1.3, 1.10)

| Base unit | Inputs | Outputs |
|---|---|---|
| FX1N-14M | 8 (X0..X7) | 6 (Y0..Y5) |
| FX1N-24M | 14 (X0..X15) | 10 (Y0..Y11) |
| FX1N-40M | 24 (X0..X27) | 16 (Y0..Y17) |
| FX1N-60M | 36 (X0..X43) | 24 (Y0..Y27) |

Expansions are numbered after the base, in octal, each unit occupying its count rounded up to 8
(24M: next input X20; 40M: X30 / Y20). FX0N- / FX2N-8ER: 4 in / 4 out used, 8 / 8 occupied.
The CPU does not report fitted units: the tool asks for them at connection. Special function blocks
(FX2N-4AD...) have no X / Y: numbered 0, 1, ... from the base for FROM / TO.

FX1N-40MR terminals: inputs on one common S/S. Sink (NPN, contact to 0V): S/S to 24V.
Source (PNP, contact to +24V): S/S to 0V. Outputs: COM0 Y0, COM1 Y1, COM2 Y2-Y3, COM3 Y4-Y7,
COM4 Y10-Y13, COM5 Y14-Y17.

## Special devices used

| Device | Meaning |
|---|---|
| M8000 | RUN |
| M8004 / D8004 | an error flag M8060..M8067 is on / which one |
| M8034 | all outputs disabled |
| M8035 / M8036 / M8037 | forced RUN mode / forced RUN / forced STOP (used for remote RUN / STOP) |
| M8061 / D8061 | hardware error (6101 RAM, 6103 I/O bus, 6104 expansion 24 V, 6105 watchdog) |
| M8064..M8067 / D8064..D8067 | parameter, syntax, structure, runtime error codes; step in D8069 |
| D8067 runtime codes | 6701 jump without destination, 6705 wrong operand type, 6706 operand out of range (index V / Z, count n), 6708 FROM / TO; the PLC keeps running (manual 6.18) |
| D8001 | CPU type + version (26100 = FX1N V1.00) |
| D8002 / D8003 | memory capacity (8 = 8k steps) / memory type (10h = built-in) |
| D8010..D8012 | scan current / min / max, x 0.1 ms |
| M8015, D8013..D8019 | clock setting; sec, min, hour, day, month, year, weekday |

To confirm on a real FX1N: remote RUN / STOP (the RUN / STOP switch may take priority), program memory
read at 8000h (`tools/mitsubishi.py programme`).
