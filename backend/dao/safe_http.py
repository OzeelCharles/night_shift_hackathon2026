"""Requêtes sortantes vers une URL fournie par un utilisateur (SEC-07, protection contre la SSRF).

Tout téléchargement d'une adresse venue de l'extérieur (lien d'une offre de stage, PDF d'exercices trouvé
en ligne…) passe par `get()`, qui :

1. n'accepte que `http` et `https`, sur les ports 80 et 443, sans identifiants dans l'URL ;
2. résout le nom **une seule fois** et refuse toute adresse non publique (`is_global` faux : boucle locale,
   réseaux privés, lien local, CGNAT, ULA IPv6…), y compris une IPv4 cachée dans une IPv6
   (mappée `::ffff:a.b.c.d`, 6to4, Teredo) ;
3. se connecte à l'adresse vérifiée (aucune seconde résolution : pas de rebinding DNS) ; en HTTPS, le
   certificat est vérifié pour le nom demandé ;
4. suit au plus 3 redirections, chacune revérifiée comme une nouvelle URL ;
5. lit au plus 5 Mo, en 10 secondes au total.

Les erreurs héritent de `requests.RequestException` : les appelants qui gèrent déjà les pannes réseau
gèrent aussi les refus.
"""
from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import threading
import time
from dataclasses import dataclass, field
from email.message import Message
from urllib.parse import urljoin, urlsplit

import requests

ALLOWED_SCHEMES = {"http": 80, "https": 443}
ALLOWED_PORTS = {80, 443}
MAX_REDIRECTS = 3
MAX_BYTES = 5 * 1024 * 1024
TIMEOUT = 10.0  # secondes, pour toute la requête (redirections comprises)
REDIRECT_CODES = {301, 302, 303, 307, 308}
CHUNK = 64 * 1024


class SafeHttpError(requests.RequestException):
    """Échec d'une requête sortante (réseau, délai, réponse invalide)."""


class Blocked(SafeHttpError):
    """URL refusée avant toute connexion (schéma, port, adresse non publique, trop de redirections)."""


class TooLarge(SafeHttpError):
    """Réponse plus grande que la limite."""


@dataclass
class Response:
    url: str
    status_code: int
    headers: dict = field(default_factory=dict)  # noms en minuscules
    content: bytes = b""

    @property
    def text(self) -> str:
        msg = Message()
        msg["content-type"] = self.headers.get("content-type", "")
        charset = msg.get_content_charset() or "utf-8"
        try:
            return self.content.decode(charset, errors="replace")
        except LookupError:
            return self.content.decode("utf-8", errors="replace")


# ---------------------------------------------------------------- contrôle des adresses
def is_public_ip(value: str) -> bool:
    """Vrai seulement pour une adresse routable sur internet (et pas une adresse interne déguisée)."""
    try:
        ip = ipaddress.ip_address(value.split("%")[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        embedded = ip.ipv4_mapped or ip.sixtofour or (ip.teredo[1] if ip.teredo else None)
        if embedded is not None and not is_public_ip(str(embedded)):
            return False
        if ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast and not ip.is_unspecified and not ip.is_reserved


@dataclass
class _Target:
    url: str
    scheme: str
    host: str  # nom (ou IP) tel qu'écrit dans l'URL, sans crochets : sert au Host et au certificat
    port: int
    ip: str  # adresse vérifiée, la seule à laquelle on se connecte
    path: str


def check_url(url: str) -> _Target:
    """Valide l'URL et résout son hôte une seule fois. Lève Blocked si elle vise autre chose qu'internet."""
    try:
        parts = urlsplit(str(url).strip())
        port = parts.port
    except ValueError as e:
        raise Blocked("URL invalide") from e
    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise Blocked("seuls http et https sont acceptés")
    if parts.username is not None or parts.password is not None:
        raise Blocked("identifiants interdits dans l'URL")
    host = (parts.hostname or "").rstrip(".")
    if not host:
        raise Blocked("hôte manquant")
    try:
        host.encode("idna")
    except UnicodeError as e:
        raise Blocked("nom d'hôte invalide") from e
    port = port or ALLOWED_SCHEMES[scheme]
    if port not in ALLOWED_PORTS:
        raise Blocked("port non autorisé")
    try:
        infos = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
    except (OSError, UnicodeError) as e:
        raise Blocked("nom d'hôte introuvable") from e
    ips = list(dict.fromkeys(str(info[4][0]) for info in infos))
    # Une seule adresse non publique suffit à refuser : un nom qui mêle public et privé est suspect.
    if not ips or not all(is_public_ip(ip) for ip in ips):
        raise Blocked("adresse non publique")
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return _Target(url=url, scheme=scheme, host=host, port=port, ip=ips[0], path=path)


# ---------------------------------------------------------------- connexions à l'adresse vérifiée
class _Watchdog:
    """Coupe la connexion à l'échéance : le délai de 10 s vaut pour toute la requête, même si le serveur
    envoie un octet de temps en temps (le délai d'une socket ne s'applique qu'à chaque lecture)."""

    def __init__(self, deadline: float):
        self.deadline = deadline
        self.fired = False
        self._timer = None

    def arm(self, sock) -> None:
        def cut():
            self.fired = True
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self._timer = threading.Timer(max(0.0, self.deadline - time.monotonic()), cut)
        self._timer.daemon = True
        self._timer.start()

    def cancel(self) -> None:
        if self._timer is not None:
            self._timer.cancel()


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host, port, ip, timeout, watchdog: _Watchdog):
        super().__init__(host, port, timeout=timeout)
        self._ip = ip
        self._watchdog = watchdog

    def _raw_socket(self):
        sock = socket.create_connection((self._ip, self.port), self.timeout)
        self._watchdog.arm(sock)  # armé avant la poignée de main TLS et les en-têtes
        return sock

    def connect(self):
        self.sock = self._raw_socket()


class _PinnedHTTPSConnection(_PinnedHTTPConnection, http.client.HTTPSConnection):
    def __init__(self, host, port, ip, timeout, watchdog: _Watchdog):
        # Magasin de certificats du système (comme dao.web.secure_session) ; nom vérifié = nom demandé
        http.client.HTTPSConnection.__init__(self, host, port, timeout=timeout, context=ssl.create_default_context())
        self._ip = ip
        self._watchdog = watchdog

    def connect(self):
        self.sock = self._context.wrap_socket(self._raw_socket(), server_hostname=self.host)


def _remaining(deadline: float) -> float:
    left = deadline - time.monotonic()
    if left <= 0:
        raise SafeHttpError("délai dépassé")
    return left


def _read_body(resp: http.client.HTTPResponse, conn, deadline: float, max_bytes: int) -> bytes:
    length = resp.getheader("Content-Length")
    if length and length.isdigit() and int(length) > max_bytes:
        raise TooLarge("réponse trop volumineuse")
    data = bytearray()
    while True:
        if conn.sock is not None:
            conn.sock.settimeout(_remaining(deadline))
        chunk = resp.read(CHUNK)
        if not chunk:
            return bytes(data)
        data += chunk
        if len(data) > max_bytes:
            raise TooLarge("réponse trop volumineuse")


def get(url: str, headers: dict | None = None, max_bytes: int = MAX_BYTES, timeout: float = TIMEOUT) -> Response:
    """GET sûr d'une URL fournie par un utilisateur. Lève Blocked, TooLarge ou SafeHttpError."""
    deadline = time.monotonic() + min(timeout, TIMEOUT)
    max_bytes = min(max_bytes, MAX_BYTES)
    sent = {k: v for k, v in (headers or {}).items() if k.lower() not in ("host", "accept-encoding", "connection")}
    sent.update({"Accept-Encoding": "identity", "Connection": "close"})
    current = url
    for _hop in range(MAX_REDIRECTS + 1):
        target = check_url(current)
        cls = _PinnedHTTPSConnection if target.scheme == "https" else _PinnedHTTPConnection
        watchdog = _Watchdog(deadline)
        conn = cls(target.host, target.port, target.ip, timeout=_remaining(deadline), watchdog=watchdog)
        try:
            conn.request("GET", target.path, headers=sent)
            resp = conn.getresponse()
            location = resp.getheader("Location")
            if resp.status in REDIRECT_CODES and location:
                current = urljoin(current, location.strip())
                continue
            body = _read_body(resp, conn, deadline, max_bytes)
            if watchdog.fired:  # corps coupé à l'échéance : incomplet, donc refusé
                raise SafeHttpError("délai dépassé")
            return Response(url=current, status_code=resp.status,
                            headers={k.lower(): v for k, v in resp.getheaders()}, content=body)
        except SafeHttpError:
            raise
        except (OSError, http.client.HTTPException, ValueError) as e:
            if watchdog.fired:
                raise SafeHttpError("délai dépassé") from e
            raise SafeHttpError(f"requête impossible : {type(e).__name__}") from e
        finally:
            watchdog.cancel()
            conn.close()
    raise Blocked("trop de redirections")
