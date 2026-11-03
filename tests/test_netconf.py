import socket
import struct
import unittest

from plc_online import netconf
from plc_online.protocols import dhcp


def discover(mac=b"\x00\x30\xde\x0a\x1b\x2c", kind=1):
    p = bytearray(236)
    p[0:4] = bytes([1, 1, 6, 0])
    p[4:8] = b"\x12\x34\x56\x78"
    p[10:12] = b"\x80\x00"
    p[28:34] = mac
    opts = bytes([53, 1, kind, 255]) if kind else b"\xff"
    return bytes(p) + dhcp.MAGIC + opts


class DhcpTest(unittest.TestCase):
    def test_parse_and_offer(self):
        req = dhcp.parse(discover())
        self.assertEqual((req["mac"], req["type"]), ("00-30-DE-0A-1B-2C", 1))
        r = dhcp.reply(req, "192.168.1.10", "192.168.1.200")
        self.assertEqual(r[0], 2)
        self.assertEqual(r[4:8], b"\x12\x34\x56\x78")
        self.assertEqual(socket.inet_ntoa(r[16:20]), "192.168.1.10")
        self.assertEqual(r[28:34], b"\x00\x30\xde\x0a\x1b\x2c")
        self.assertIn(bytes([53, 1, dhcp.OFFER]), r[240:])
        self.assertIn(bytes([1, 4, 255, 255, 255, 0]), r[240:])

    def test_request_ack_and_bootp(self):
        self.assertIn(bytes([53, 1, dhcp.ACK]), dhcp.reply(dhcp.parse(discover(kind=3)), "10.0.0.5", "10.0.0.200")[240:])
        boot = dhcp.parse(discover(kind=0))
        self.assertIsNone(boot["type"])
        self.assertNotIn(bytes([53, 1]), dhcp.reply(boot, "10.0.0.5", "10.0.0.200")[240:243])


class NetconfTest(unittest.TestCase):
    def test_adapters(self):
        a = '[{"Name":"Ethernet","InterfaceDescription":"Intel","MacAddress":"AA-BB","Status":"Up"},' \
            '{"Name":"Wi-Fi","InterfaceDescription":"Intel AX","MacAddress":"CC","Status":"Disconnected"}]'
        b = '{"InterfaceAlias":"Ethernet","IPAddress":"169.254.3.4","PrefixLength":16,"PrefixOrigin":3}'
        c = '[{"InterfaceAlias":"Ethernet","Dhcp":1},{"InterfaceAlias":"Wi-Fi","Dhcp":0}]'
        cards = netconf.parse_adapters(a, b, c)
        self.assertEqual(cards[0]["adresses"][0]["ip"], "169.254.3.4")
        self.assertTrue(cards[0]["dhcp"])
        self.assertEqual(cards[1]["etat"], "debranche")
        self.assertFalse(cards[1]["dhcp"])

    def test_quote(self):
        self.assertEqual(netconf._q("Ethernet 2"), "'Ethernet 2'")
        self.assertEqual(netconf._q("it's"), "'it''s'")


class SweepTest(unittest.TestCase):
    def test_keeps_only_the_network_found(self):
        from plc_online.web import wago_web
        calls = []

        class FakeNet:
            WAGO_OUI = "00-30-DE"
            NetError = Exception

            @staticmethod
            def adapter(name):
                return {"nom": name, "dhcp": True, "adresses": [{"ip": "169.254.1.1"}]}

            @staticmethod
            def add_addresses(name, ips):
                calls.append(("add", ips))

            @staticmethod
            def remove_addresses(name, ips):
                calls.append(("remove", ips))

            @staticmethod
            def set_dhcp(name):
                calls.append(("dhcp",))

        old_net, old_scan = wago_web._net, wago_web.wg.scan
        wago_web._net = lambda: FakeNet
        wago_web.wg.scan = lambda targets, t: [{"ip": "192.168.1.17", "modele": "WAGO 750-352", "wago": True}] \
            if "192.168.1.17" in targets else []
        try:
            r = wago_web.sweep("Ethernet")
        finally:
            wago_web._net, wago_web.wg.scan = old_net, old_scan
        self.assertEqual(r["pc"], ["192.168.1.253"])
        self.assertEqual(calls[1][0], "remove")
        self.assertNotIn("192.168.1.253", calls[1][1])
        self.assertEqual(len(calls[1][1]), 8)


if __name__ == "__main__":
    unittest.main()
