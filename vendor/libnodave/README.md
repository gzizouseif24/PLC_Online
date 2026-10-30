# libnodave

`libnodave.dll`: 32-bit Windows build of libnodave (Thomas Hergenhahn, GNU LGPL),
used by `plc_online/drivers/siemens.py` to reach an S7-300 through STEP 7's S7ONLINE driver.

SHA-256: `b6920c8a3ba9493eabb41d450d51485115efe75a7110493e0d0b0e1144a35f95`

It loads `S7onlinx.dll` at run time. That file belongs to Siemens, is installed with STEP 7,
and is **not** in this repo.
