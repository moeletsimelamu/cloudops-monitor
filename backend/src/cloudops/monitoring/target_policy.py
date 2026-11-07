"""Which destinations may be monitored: the SSRF guard (docs/SECURITY.md §3).

Two enforcement points use this module: URL validation when an application is registered or
changed (``validate_url``), and the address check made for every connection the probe opens
(``blocked_reason``), which catches DNS answers that change after registration.
"""

import ipaddress
import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

from cloudops.config import Settings

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

MAX_URL_LENGTH = 2048

# Never reachable, even when allowlisted: link-local (incl. cloud metadata services),
# "this network", multicast, broadcast and reserved ranges.
_ALWAYS_BLOCKED = tuple(
    ipaddress.ip_network(n)
    for n in (
        "0.0.0.0/8",
        "169.254.0.0/16",  # link-local; 169.254.169.254 is the AWS/GCP/Azure metadata service
        "100.100.100.200/32",  # Alibaba Cloud metadata service
        "224.0.0.0/4",
        "240.0.0.0/4",
        "255.255.255.255/32",
        "::/128",
        "fe80::/10",
        "fd00:ec2::254/128",  # AWS metadata service over IPv6
        "ff00::/8",
    )
)

_HOST_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")


class TargetPolicyError(ValueError):
    """The URL or destination is not allowed. The message is shown to the operator."""


@dataclass(frozen=True)
class Target:
    scheme: Literal["http", "https"]
    host: str  # lower case; IPv6 without brackets
    port: int
    is_ip_literal: bool


# NAT64's well-known prefix: 64:ff9b::a.b.c.d is translated to the IPv4 address a.b.c.d by a NAT64
# gateway, so 64:ff9b::a9fe:a9fe reaches 169.254.169.254 on an IPv6-only network that has one.
_NAT64 = ipaddress.ip_network("64:ff9b::/96")


def _embedded_ipv4(address: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    """The IPv4 address an IPv6 transition address leads to, if it has one."""
    if address in _NAT64:
        return ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
    if address.sixtofour is not None:  # 2002:a.b.c.d::/48
        return address.sixtofour
    if address.teredo is not None:  # 2001::/32, (server, client)
        return address.teredo[1]
    if 1 < int(address) < 2**32:  # deprecated IPv4-compatible form ::a.b.c.d
        return ipaddress.IPv4Address(int(address))
    return None


def _forms(address: IPAddress) -> tuple[IPAddress, ...]:
    """Every address a connection to ``address`` may end up at, all of which are checked.

    An IPv4-mapped address (::ffff:a.b.c.d) is only ever the IPv4 address. A transition address
    is checked as itself and as the IPv4 address inside it, so either form being blocked blocks
    it: Python's ``is_global`` treats 64:ff9b::7f00:1 (NAT64 for 127.0.0.1) as public.
    """
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            return (address.ipv4_mapped,)
        embedded = _embedded_ipv4(address)
        if embedded is not None:
            return (address, embedded)
    return (address,)


def _parse_ip(host: str) -> IPAddress | None:
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def validate_url(url: str) -> Target:
    """Structural rules that apply whatever the policy."""
    if len(url) > MAX_URL_LENGTH:
        raise TargetPolicyError(f"The URL is longer than {MAX_URL_LENGTH} characters.")
    if any(ch.isspace() for ch in url):
        raise TargetPolicyError("The URL must not contain spaces.")
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise TargetPolicyError("Only http:// and https:// URLs can be monitored.")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise TargetPolicyError(
            "Credentials in the URL (user:password@host) are not allowed: they would be stored "
            "and displayed in plain text."
        )
    if parts.fragment:
        raise TargetPolicyError("Remove the #fragment: it is never sent to the server.")
    host = (parts.hostname or "").rstrip(".")
    if not host:
        raise TargetPolicyError("The URL has no host name.")
    if not host.isascii():
        raise TargetPolicyError(
            "Internationalised host names are not supported; use the ASCII (xn--) form."
        )
    try:
        port = parts.port
    except ValueError:
        raise TargetPolicyError("The port must be a number from 1 to 65535.") from None
    if port == 0:
        raise TargetPolicyError("The port must be a number from 1 to 65535.")

    address = _parse_ip(host)
    if address is None:
        labels = host.split(".")
        if not all(_HOST_LABEL.fullmatch(label) for label in labels):
            raise TargetPolicyError(f"'{host}' is not a valid host name.")
        if labels[-1].isdigit():
            # Forms like 2130706433 or 127.1 are resolved to IP addresses by some resolvers.
            raise TargetPolicyError("Write IP addresses in standard dotted form.")
    return Target(
        scheme="https" if scheme == "https" else "http",
        host=host,
        port=port if port is not None else (443 if scheme == "https" else 80),
        is_ip_literal=address is not None,
    )


def _parse_entry(entry: str) -> tuple[str, int]:
    host, _, port = entry.rpartition(":")
    return host.strip("[]").rstrip("."), int(port)


class TargetPolicy:
    def __init__(self, mode: Literal["allowlist", "public"], allowed_targets: list[str]) -> None:
        self.mode = mode
        self.allowed = frozenset(_parse_entry(entry.lower()) for entry in allowed_targets)

    @classmethod
    def from_settings(cls, settings: Settings) -> "TargetPolicy":
        return cls(settings.target_policy, settings.allowed_targets)

    def is_listed(self, target: Target) -> bool:
        return (target.host, target.port) in self.allowed

    def check(self, url: str) -> Target:
        """Validate a URL against the policy, before any DNS lookup.

        In public mode the resolved addresses are checked separately (``blocked_reason``),
        because a host name says nothing about where it points.
        """
        target = validate_url(url)
        if self.is_listed(target):
            if target.is_ip_literal:
                reason = always_blocked_reason(ipaddress.ip_address(target.host))
                if reason:
                    raise TargetPolicyError(f"{target.host} is {reason} and is never monitored.")
            return target
        if self.mode == "allowlist":
            raise TargetPolicyError(
                f"{target.host}:{target.port} is not in CLOUDOPS_ALLOWED_TARGETS. The operator "
                "must add it to the server configuration before it can be monitored."
            )
        if not (target.port in (80, 443) or target.port >= 1024):
            raise TargetPolicyError("Public targets must use port 80, 443 or 1024-65535.")
        if target.is_ip_literal:
            reason = self.blocked_reason(target.host, listed=False)
            if reason:
                raise TargetPolicyError(f"{target.host} is {reason}.")
        return target

    def blocked_reason(self, ip: str, *, listed: bool) -> str | None:
        """Why a connection to ``ip`` is refused, or None if it is allowed.

        Allowlisted destinations may be private or loopback (an explicit operator decision);
        everything else must be a globally routable address.
        """
        address = _parse_ip(ip.split("%", 1)[0])  # drop an IPv6 zone ID
        if address is None:
            return "not an IP address"
        reason = always_blocked_reason(address)
        if reason or listed:
            return reason
        for form in _forms(address):
            # Site-local (fec0::/10) is deprecated but still routed by some networks, and
            # Python counts it as global.
            site_local = isinstance(form, ipaddress.IPv6Address) and form.is_site_local
            if not form.is_global or site_local:
                return "a private, loopback or reserved address"
        return None


def always_blocked_reason(address: IPAddress) -> str | None:
    for form in _forms(address):
        for network in _ALWAYS_BLOCKED:
            if form.version == network.version and form in network:
                if form.is_link_local:
                    return "a link-local address (this includes cloud metadata services)"
                return "a reserved, multicast or metadata-service address"
    return None
