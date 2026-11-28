"""Outils communs aux tests de sécurité (attaquant du duel).

- `AppSecuTestCase` : application jetable (base et dépôts dans un dossier temporaire), web et IA coupés,
  aucune clé lue dans instance/ (SECRET_KEY et identifiants admin fournis par l'environnement du test).
- `GardeReseau` : intercepte la résolution DNS et toutes les connexions sortantes. Seules les connexions
  vers la boucle locale partent réellement ; tout le reste est bloqué et noté. Un nom « public » peut être
  simulé (DNS factice) et redirigé vers un serveur local (`detours`).
- `ServeurLocal` : petit serveur HTTP sur 127.0.0.1 qui compte les requêtes reçues.
"""
from __future__ import annotations

import http.server
import ipaddress
import os
import socket
import tempfile
import threading
import unittest
from unittest import mock

from backend import create_app
from backend.dao.db import get_db

MOT_DE_PASSE = "Tisane-Velo-Granit-42"
ADMIN_EMAIL = "admin@test.fr"
ADMIN_MDP = "Phare-Ecluse-Bruine-77"


class AppSecuTestCase(unittest.TestCase):
    """Application de test isolée. `config` peut être surchargé par une sous-classe."""

    config: dict = {}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)  # Windows : fichiers encore ouverts
        env = mock.patch.dict(os.environ, {"SECRET_KEY": "cle-de-test-uniquement-" + "x" * 16,
                                           "ADMIN_EMAIL": ADMIN_EMAIL, "ADMIN_PASSWORD": ADMIN_MDP})
        env.start()
        self.addCleanup(env.stop)
        cfg = {"TESTING": True, "WTF_CSRF_DISABLED": True, "ENABLE_WEB": False,
               "REVISION_AI": False, "STAGES_AI": False, "VISION_AI": False,
               "DATABASE": os.path.join(self.tmp.name, "t.sqlite"),
               "UPLOAD_FOLDER": os.path.join(self.tmp.name, "up")}
        cfg.update(self.config)
        self.app = create_app(cfg)
        self.app.instance_path = self.tmp.name  # jamais les vraies clés d'API de instance/
        self.client = self.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    # ------------------------------------------------------------ comptes
    def inscrire(self, email, client=None, username=None, password=MOT_DE_PASSE, **kwargs):
        client = client or self.client
        return client.post("/inscription", data={"email": email, "username": username or "Eleve " + email.split("@")[0],
                                                  "password": password, "password2": password,
                                                  "level": "licence", "school": "ENSAI"}, **kwargs)

    def connecter(self, email, password=MOT_DE_PASSE, client=None):
        client = client or self.client
        client.post("/deconnexion")
        return client.post("/connexion", data={"email": email, "password": password})

    def q(self, sql, *args):
        with self.app.app_context():
            return [dict(r) for r in get_db().execute(sql, args)]


# ---------------------------------------------------------------- réseau
_ATON = {"0x7f000001": "127.0.0.1", "2130706433": "127.0.0.1", "127.1": "127.0.0.1",
         "0177.0.0.1": "127.0.0.1", "0x7f.0.0.1": "127.0.0.1", "017700000001": "127.0.0.1"}


def ip_non_publique(ip: str) -> bool:
    try:
        return not ipaddress.ip_address(ip.split("%")[0]).is_global
    except ValueError:
        return True


class GardeReseau:
    """Remplace socket.getaddrinfo et socket.socket.connect le temps d'un bloc `with`.

    dns     : {nom: [ip, ...] ou fonction() -> [ip, ...]} (noms factices, aucune requête DNS réelle)
    detours : {(ip, port): (ip_locale, port_local)} pour simuler un hôte public par un serveur local
    internes : {(ip, port): (ip_locale, port_local)} pour simuler un service interne sur un port privilégié
               (ex. 127.0.0.1:80) ; ces connexions comptent comme internes
    """

    def __init__(self, dns=None, detours=None, internes=None):
        self.dns = dns or {}
        self.detours = detours or {}
        self.internes = internes or {}
        self.connexions: list[tuple[str, int]] = []
        self.resolutions: list[str] = []
        self._patches = []

    # -- DNS factice : IP littérales, localhost, formes inet_aton, noms déclarés ; tout le reste échoue
    def _ips(self, host: str) -> list[str]:
        h = host.strip("[]")
        if h in self.dns:
            v = self.dns[h]
            return list(v() if callable(v) else v)
        try:
            return [str(ipaddress.ip_address(h.split("%")[0]))]
        except ValueError:
            pass
        if h.lower() in ("localhost", "localhost.localdomain"):
            return ["127.0.0.1"]
        if h.lower() in _ATON:
            return [_ATON[h.lower()]]
        raise socket.gaierror(socket.EAI_NONAME, f"résolution interdite pendant le test : {host}")

    def getaddrinfo(self, host, port, family=0, type=0, proto=0, flags=0):
        if isinstance(host, bytes):
            host = host.decode()
        if host is None:
            host = "localhost"
        self.resolutions.append(host)
        port = int(port) if port not in (None, "") and str(port).isdigit() else 0
        out = []
        for ip in self._ips(host):
            fam = socket.AF_INET6 if ":" in ip else socket.AF_INET
            if family not in (0, socket.AF_UNSPEC, fam):
                continue
            addr = (ip, port, 0, 0) if fam == socket.AF_INET6 else (ip, port)
            out.append((fam, type or socket.SOCK_STREAM, proto or socket.IPPROTO_TCP, "", addr))
        if not out:
            raise socket.gaierror(socket.EAI_NONAME, f"aucune adresse pour {host}")
        return out

    def _connect(self, original):
        garde = self

        def connect(sock, address):
            if not isinstance(address, tuple):
                return original(sock, address)
            ip, port = str(address[0]), int(address[1])
            garde.connexions.append((ip, port))
            if (ip, port) in garde.detours:
                return original(sock, garde.detours[(ip, port)])
            if (ip, port) in garde.internes:
                return original(sock, garde.internes[(ip, port)])
            try:
                boucle = ipaddress.ip_address(ip.split("%")[0]).is_loopback
            except ValueError:
                boucle = False
            if boucle:
                return original(sock, address)
            raise ConnectionRefusedError(f"connexion vers {ip}:{port} bloquée par le test (aucun réseau externe)")
        return connect

    def connexions_internes(self) -> list[tuple[str, int]]:
        """Connexions tentées vers une adresse non publique (hors détours simulant un hôte public)."""
        return [(ip, p) for ip, p in self.connexions if ip_non_publique(ip) and (ip, p) not in self.detours]

    def __enter__(self):
        orig_connect = socket.socket.connect
        orig_connect_ex = socket.socket.connect_ex
        connect = self._connect(orig_connect)

        def connect_ex(sock, address):
            try:
                connect(sock, address)
                return 0
            except OSError as e:
                return e.errno or 111

        self._patches = [mock.patch("socket.getaddrinfo", self.getaddrinfo),
                         mock.patch.object(socket.socket, "connect", connect),
                         mock.patch.object(socket.socket, "connect_ex", connect_ex)]
        del orig_connect_ex
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.stop()
        return False


class ServeurLocal:
    """Serveur HTTP sur 127.0.0.1 (port libre). `pages` : {chemin: (statut, en-têtes, corps)}."""

    def __init__(self, pages=None, defaut=(200, {"Content-Type": "text/html; charset=utf-8"}, b"")):
        self.pages = pages or {}
        self.defaut = defaut
        self.requetes: list[str] = []
        serveur = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                serveur.requetes.append(self.path)
                status, headers, body = serveur.pages.get(self.path, serveur.defaut)
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_HEAD = do_GET

            def log_message(self, *args):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()
        return False


class ServeurBrut:
    """Serveur TCP sur 127.0.0.1 : `repondre(conn)` écrit la réponse brute (octets, délais…) sur chaque
    connexion. Compte les connexions reçues. Sert aux réponses que http.server ne sait pas produire
    (pas de Content-Length, envoi au compte-gouttes)."""

    def __init__(self, repondre):
        self.repondre = repondre
        self.connexions = 0
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(8)
        self.port = self._sock.getsockname()[1]
        self._arret = threading.Event()
        self._thread = threading.Thread(target=self._boucle, daemon=True)

    def _boucle(self):
        self._sock.settimeout(0.2)
        while not self._arret.is_set():
            try:
                conn, _ = self._sock.accept()
            except OSError:
                continue
            self.connexions += 1
            threading.Thread(target=self._servir, args=(conn,), daemon=True).start()

    def _servir(self, conn):
        try:
            conn.settimeout(5)
            try:
                conn.recv(65536)  # requête (ignorée)
            except OSError:
                pass
            self.repondre(conn, self._arret)
        except OSError:
            pass
        finally:
            conn.close()

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._arret.set()
        self._sock.close()
        return False


class FauxGroq:
    """Remplace l'appel HTTP à Groq (web.secure_session().post) : aucune requête ne part.

    `reponse(body)` renvoie le texte que le « modèle » répond (contenu du message), à partir du corps
    JSON envoyé (body["messages"], body["response_format"]…). Les appels sont notés dans `appels`.
    Clé factice : mettre "GROQ_API_KEY": CLE_FACTICE dans la configuration de l'application de test."""

    def __init__(self, reponse):
        self.reponse = reponse
        self.appels: list[dict] = []
        self._patch = None

    def _post(self, url, timeout=None, headers=None, json=None, **kw):
        self.appels.append(json or {})
        contenu = self.reponse(json or {})
        r = mock.Mock(status_code=200, text="")
        r.json.return_value = {"choices": [{"message": {"content": contenu}}]}
        return r

    def __enter__(self):
        session = mock.Mock()
        session.post.side_effect = self._post
        self._patch = mock.patch("backend.dao.web.secure_session", return_value=session)
        self._patch.start()
        return self

    def __exit__(self, *exc):
        self._patch.stop()
        return False


CLE_FACTICE = "gsk_cle_factice_pour_les_tests"
