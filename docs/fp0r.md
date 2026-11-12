# Panasonic FP0R: I/O, expansion units, special relays

Source: Panasonic *FP0R User's Manual* WUME-FP0R-03 (2025.10), checked page by page.

## I/O numbers (manual 4.2, 4.3)

X and Y are counted 16 per word: X20..X2F = WX2. The I/O allocation is automatic, by position
(expansion 1 is the unit next to the CPU). Up to 3 expansion units on the right.

| CPU | Inputs | Outputs |
|---|---|---|
| C10 | X0..X5 | Y0..Y3 |
| C14 | X0..X7 | Y0..Y5 |
| C16 | X0..X7 | Y0..Y7 |
| C32 / T32 / F32 | X0..XF | Y0..YF |

| Expansion unit | Inputs | Outputs |
|---|---|---|
| E8X | 8 (X20..X27) | - |
| E8R | 4 (X20..X23) | 4 (Y20..Y23) |
| E8YT / E8YP / E8YR | - | 8 (Y20..Y27) |
| E16X | 16 (X20..X2F) | - |
| E16R / E16T / E16P | 8 (X20..X27) | 8 (Y20..Y27) |
| E16YT / E16YP | - | 16 (Y20..Y2F) |
| E32T / E32P | 16 (X20..X2F) | 16 (Y20..Y2F) |
| AD4/AD8, DA4, A21/A42, TC4 | words WX2 / WX3 | words WY2 / WY3 |

Expansion 2 and 3: same with X40 / Y40 (WX4..WX5) and X60 / Y60 (WX6..WX7).

The CPU does **not** report which units are fitted: the tool asks for the model of each position
at connection (Extension 1 / 2 / 3). It does flag a change since power-up: **R9004** (I/O
verification error) and **DT90010** (bit 0..2 = expansion 1..3).

## E16P wiring (manual 3.4, page 3-9)

10-pin MIL connector, front view, pin 1 at the triangle:

| Input connector | | Output connector | |
|---|---|---|---|
| X0 | X1 | Y0 | Y1 |
| X2 | X3 | Y2 | Y3 |
| X4 | X5 | Y4 | Y5 |
| X6 | X7 | Y6 | Y7 |
| COM | COM | (+) | (-) |

The labels are local (X0..X7 on the unit = X20..X27 in expansion 1, X40..X47 in expansion 2).
Inputs: 24 V DC (21.6..26.4 V), about 4.7 mA, 8 points per common, either polarity on COM; the
two COM pins are joined inside. Outputs PNP: 24 V supply on (+) / (-), load between Y and 0 V,
0.3 A per point, 1 A per common.

## Special relays and registers used by the tool (manual 14)

| Address | Meaning |
|---|---|
| R9000 | self-diagnostic error, code in DT90000 |
| R9004 | I/O verification error, units in DT90010 |
| R9007 / R9008 | operation error (hold / latest), step in DT90017 / DT90018 |
| R900E | tool port communication error |
| R900F | constant scan exceeded; **also always ON when constant scan is not used (system register 34 = 0)** |
| R9010 / R9013 / R901C | always on / first scan / 1 s clock |
| R9020 | mode switch on RUN |
| R9026 | F149 MSG running |
| R9029 | forcing active |
| DT90022 / 23 / 24 | scan time current / min / max, x 0.1 ms |

R9027 is "not used" on the FP0R.
