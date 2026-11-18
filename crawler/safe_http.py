"""DNS-pinned, public-address-only HTTP transport for Everything Hub crawlers."""

import http.client
import ipaddress
import socket
import urllib.parse
import urllib.request


class UnsafeTargetError(RuntimeError):
    """Raised when a URL cannot be pinned to a public network destination."""


def resolve_public_target(url, test_mode=False):
    """Resolve at request time and return the exact validated socket address."""
    try:
        parsed = urllib.parse.urlsplit(url)
        host = parsed.hostname
        if (
            parsed.scheme.lower() not in ("http", "https")
            or not host
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise UnsafeTargetError("URL authority is not an unauthenticated HTTP(S) target")
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
        records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except UnsafeTargetError:
        raise
    except (OSError, TypeError, ValueError) as error:
        raise UnsafeTargetError("DNS resolution failed or URL authority is invalid") from error

    candidates = []
    for family, socktype, proto, _canonname, sockaddr in records:
        if family not in (socket.AF_INET, socket.AF_INET6) or socktype != socket.SOCK_STREAM:
            continue
        try:
            address = ipaddress.ip_address(str(sockaddr[0]).split("%", 1)[0])
        except (IndexError, TypeError, ValueError) as error:
            raise UnsafeTargetError("DNS returned an invalid socket address") from error
        if not address.is_global and not (test_mode and address.is_loopback):
            raise UnsafeTargetError("DNS resolved to a non-public destination")
        candidates.append((family, socktype, proto, sockaddr))

    if not candidates:
        raise UnsafeTargetError("DNS returned no usable public socket address")
    return candidates[0]


def is_safe_target(url, test_mode=False):
    """Preflight a URL; every actual connection resolves and pins again."""
    try:
        resolve_public_target(url, test_mode=test_mode)
        return True
    except UnsafeTargetError:
        return False


def _open_pinned_socket(pinned_target, timeout, source_address):
    family, socktype, proto, sockaddr = pinned_target
    sock = socket.socket(family, socktype, proto)
    try:
        if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
            sock.settimeout(timeout)
        if source_address:
            sock.bind(source_address)
        # Use the exact validated sockaddr; socket.connect never resolves the hostname.
        sock.connect(sockaddr)
        return sock
    except Exception:
        sock.close()
        raise


class PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host, pinned_target, **kwargs):
        self._pinned_target = pinned_target
        super().__init__(host, **kwargs)

    def connect(self):
        if self._tunnel_host:
            raise OSError("proxy tunnels are disabled for pinned crawler requests")
        self.sock = _open_pinned_socket(
            self._pinned_target, self.timeout, self.source_address
        )


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, pinned_target, **kwargs):
        self._pinned_target = pinned_target
        super().__init__(host, **kwargs)

    def connect(self):
        if self._tunnel_host:
            raise OSError("proxy tunnels are disabled for pinned crawler requests")
        raw_socket = _open_pinned_socket(
            self._pinned_target, self.timeout, self.source_address
        )
        try:
            # Keep the original hostname for TLS SNI and certificate validation.
            self.sock = self._context.wrap_socket(
                raw_socket, server_hostname=self.host
            )
        except Exception:
            raw_socket.close()
            raise


class PinnedHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, test_mode=False, debuglevel=0):
        self.test_mode = test_mode
        super().__init__(debuglevel)

    def http_open(self, req):
        pinned = resolve_public_target(req.full_url, test_mode=self.test_mode)
        return self.do_open(
            lambda host, **kwargs: PinnedHTTPConnection(host, pinned, **kwargs),
            req,
        )


class PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, test_mode=False, debuglevel=0, context=None, check_hostname=None):
        self.test_mode = test_mode
        super().__init__(debuglevel, context, check_hostname)

    def https_open(self, req):
        pinned = resolve_public_target(req.full_url, test_mode=self.test_mode)
        return self.do_open(
            lambda host, **kwargs: PinnedHTTPSConnection(host, pinned, **kwargs),
            req,
            context=self._context,
        )


def build_pinned_opener(redirect_handler, test_mode=False):
    """Build direct HTTP(S) transport; environment proxies cannot bypass IP pinning."""
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        PinnedHTTPHandler(test_mode=test_mode),
        PinnedHTTPSHandler(test_mode=test_mode),
        redirect_handler,
    )
