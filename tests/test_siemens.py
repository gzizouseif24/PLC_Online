"""Siemens driver checks that run anywhere (the real link needs Windows + STEP 7)."""
import ctypes
import unittest

from plc_online.protocols.s7 import DEFAULT_DLL, S7Error, load_dll, word_be


class SiemensTests(unittest.TestCase):
    def test_big_endian_word(self):
        self.assertEqual(word_be(bytes([0x04, 0xD2, 0, 0]), 0), 1234)  # MW0 from the report

    def test_dll_is_shipped(self):
        self.assertTrue(DEFAULT_DLL.is_file())

    @unittest.skipIf(ctypes.sizeof(ctypes.c_void_p) == 4 and hasattr(ctypes, "WinDLL"),
                     "32-bit Windows: the DLL can really load here")
    def test_clear_error_outside_32bit_windows(self):
        with self.assertRaises(S7Error):
            load_dll()


if __name__ == "__main__":
    unittest.main()
