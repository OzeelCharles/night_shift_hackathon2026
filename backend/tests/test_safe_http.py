"""dao/safe_http (SEC-07) : contrôle des URL, redirections et limites, sans aucun réseau externe.

Un nom « public » factice (8.8.8.8) est servi par un petit serveur local : la résolution et la connexion
sont simulées, aucune requête ne quitte la machine.
"""
import http.server
import socket
import threading
import unittest
from unittest import mock

from backend.dao import safe_http


class _Server:
    def __init__(self, handler):
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def _fake_dns(mapping):
    def getaddrinfo(host, port, *args, **kwargs):
        if host == "127.0.0.1":  # le serveur local de test
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (host, port))]
        if host not in mapping:
            raise socket.gaierror("inconnu")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in mapping[host]]
    return getaddrinfo


class CheckUrlTests(unittest.TestCase):
    def test_refused_before_any_resolution(self):
        with mock.patch("socket.getaddrinfo") as dns:
            for url in ("file:///etc/passwd", "gopher://x/", "ftp://x/", "http://user:pw@exemple.fr/",
                        "http://exemple.fr:8080/", "http://exemple.fr:22/", "http:///x", "http://exemple.fr:99999/"):
                with self.subTest(url=url), self.assertRaises(safe_http.Blocked):
                    safe_http.check_url(url)
            dns.assert_not_called()

    def test_non_public_addresses(self):
        for ip in ("127.0.0.1", "10.0.0.1", "172.16.0.1", "192.168.1.1", "169.254.169.254", "100.64.0.1",
                   "0.0.0.0", "::1", "fd00::1", "fe80::1", "::ffff:127.0.0.1", "::ffff:10.0.0.1",
                   "2002:7f00:1::", "224.0.0.1", "255.255.255.255"):
            with self.subTest(ip=ip):
                self.assertFalse(safe_http.is_public_ip(ip))
        self.assertTrue(safe_http.is_public_ip("8.8.8.8"))
        self.assertTrue(safe_http.is_public_ip("2606:4700:4700::1111"))

    def test_one_private_answer_is_enough_to_refuse(self):
        with mock.patch("socket.getaddrinfo", _fake_dns({"mixte.example": ["8.8.8.8", "10.0.0.1"]})):
            with self.assertRaises(safe_http.Blocked):
                safe_http.check_url("http://mixte.example/")

    def test_single_resolution_and_pinned_ip(self):
        dns = mock.Mock(side_effect=_fake_dns({"ok.example": ["8.8.8.8"]}))
        with mock.patch("socket.getaddrinfo", dns):
            t = safe_http.check_url("https://ok.example/a?b=1")
        self.assertEqual((t.host, t.ip, t.port, t.path), ("ok.example", "8.8.8.8", 443, "/a?b=1"))
        self.assertEqual(dns.call_count, 1)


class GetTests(unittest.TestCase):
    """Le serveur local joue le rôle de 8.8.8.8:80 (create_connection détourné)."""

    def setUp(self):
        pages = self.pages = {}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                status, headers, body = pages.get(self.path, (404, {}, b""))
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.server = _Server(Handler)
        self.addCleanup(self.server.close)
        real = socket.create_connection
        self.connections = []

        def create_connection(address, timeout=None, *a, **k):
            self.connections.append(address)
            if address == ("8.8.8.8", 80):
                return real(("127.0.0.1", self.server.port), timeout)
            raise ConnectionRefusedError(address)

        for p in (mock.patch("socket.getaddrinfo", _fake_dns({"pub.example": ["8.8.8.8"],
                                                                "prive.example": ["10.0.0.5"]})),
                  mock.patch("socket.create_connection", create_connection)):
            p.start()
            self.addCleanup(p.stop)

    def test_page_read_with_host_header(self):
        self.pages["/offre"] = (200, {"Content-Type": "text/html; charset=utf-8"}, "Été".encode())
        r = safe_http.get("http://pub.example/offre")
        self.assertEqual((r.status_code, r.text), (200, "Été"))
        self.assertEqual(self.connections, [("8.8.8.8", 80)])

    def test_redirects_rechecked_and_limited(self):
        self.pages["/vers-prive"] = (302, {"Location": "http://prive.example/"}, b"")
        with self.assertRaises(safe_http.Blocked):
            safe_http.get("http://pub.example/vers-prive")
        self.assertEqual(self.connections, [("8.8.8.8", 80)], "aucune connexion vers l'adresse privée")
        for i in range(5):
            self.pages[f"/r{i}"] = (302, {"Location": f"/r{i + 1}"}, b"")
        self.pages["/r5"] = (200, {}, b"fin")
        with self.assertRaises(safe_http.Blocked):
            safe_http.get("http://pub.example/r0")
        self.pages["/r2"] = (200, {}, b"ok")  # 2 redirections : accepté
        self.assertEqual(safe_http.get("http://pub.example/r0").content, b"ok")

    def test_size_limit(self):
        self.pages["/gros"] = (200, {}, b"x" * 2048)
        with self.assertRaises(safe_http.TooLarge):
            safe_http.get("http://pub.example/gros", max_bytes=1024)


if __name__ == "__main__":
    unittest.main()
