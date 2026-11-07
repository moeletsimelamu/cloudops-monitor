import pytest

from cloudops.monitoring.target_policy import TargetPolicy, TargetPolicyError, validate_url

ALLOWLIST = TargetPolicy("allowlist", ["localhost:8310", "[::1]:8000", "10.0.0.5:80"])
PUBLIC = TargetPolicy("public", ["localhost:8310"])


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("ftp://example.com/health", "Only http:// and https://"),
        ("file:///etc/passwd", "Only http:// and https://"),
        ("http://user:pw@localhost:8310/health", "Credentials in the URL"),
        ("http://@localhost:8310/", "Credentials in the URL"),
        ("http://localhost:8310/health#top", "#fragment"),
        ("http:///health", "no host name"),
        ("http://localhost:99999/", "port must be a number"),
        ("http://localhost:0/", "port must be a number"),
        ("http://bad_host.example/", "not a valid host name"),
        ("http://-bad.example/", "not a valid host name"),
        ("http://2130706433/", "dotted form"),
        ("http://127.1/", "dotted form"),
        ("http://bücher.example/", "Internationalised host names"),
        ("http://localhost:8310/he alth", "must not contain spaces"),
        ("http://localhost:8310/" + "a" * 2048, "longer than 2048"),
    ],
)
def test_structural_rules(url: str, message: str) -> None:
    with pytest.raises(TargetPolicyError, match=message):
        validate_url(url)


def test_normalisation() -> None:
    target = validate_url("HTTPS://API.Example.COM./health?full=1")
    assert (target.scheme, target.host, target.port) == ("https", "api.example.com", 443)
    assert validate_url("http://[::1]:8000/").host == "::1"
    assert validate_url("http://localhost/").port == 80


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8310/health",
        "http://LOCALHOST:8310/health",
        "http://localhost.:8310/health",
        "http://[::1]:8000/api/health",
        "http://10.0.0.5/health",
    ],
)
def test_allowlisted_targets_are_accepted(url: str) -> None:
    ALLOWLIST.check(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8311/health",  # right host, wrong port
        "http://127.0.0.1:8310/health",  # same machine, but a different name is a different entry
        "https://example.com/health",  # public hosts need public mode
    ],
)
def test_unlisted_targets_are_refused_in_allowlist_mode(url: str) -> None:
    with pytest.raises(TargetPolicyError, match="not in CLOUDOPS_ALLOWED_TARGETS"):
        ALLOWLIST.check(url)


def test_metadata_addresses_are_refused_even_when_allowlisted() -> None:
    policy = TargetPolicy("allowlist", ["169.254.169.254:80"])
    with pytest.raises(TargetPolicyError, match="link-local"):
        policy.check("http://169.254.169.254/latest/meta-data/")


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("http://10.1.2.3/", "private, loopback or reserved"),
        ("http://127.0.0.1:8080/", "private, loopback or reserved"),
        ("http://[::ffff:127.0.0.1]:8080/", "private, loopback or reserved"),
        ("http://169.254.169.254/", "link-local"),
        ("http://example.com:22/", "port 80, 443 or 1024-65535"),
    ],
)
def test_public_mode_refuses_non_public_literals_and_low_ports(url: str, message: str) -> None:
    with pytest.raises(TargetPolicyError, match=message):
        PUBLIC.check(url)


def test_public_mode_accepts_public_names_and_the_allowlist() -> None:
    PUBLIC.check("https://status.example.com/health")
    PUBLIC.check("http://example.com:8443/health")
    PUBLIC.check("http://localhost:8310/health")


@pytest.mark.parametrize(
    ("ip", "listed", "blocked"),
    [
        ("93.184.216.34", False, False),
        ("2606:2800:220:1:248:1893:25c8:1946", False, False),
        ("127.0.0.1", False, True),
        ("127.0.0.1", True, False),  # loopback is fine when the operator allowlisted it
        ("::1", True, False),
        ("10.0.0.5", False, True),
        ("172.16.3.4", False, True),
        ("192.168.1.1", False, True),
        ("100.64.0.1", False, True),  # carrier-grade NAT
        ("fc00::1", False, True),  # unique local
        ("192.0.2.10", False, True),  # documentation range
        ("::ffff:10.0.0.1", False, True),  # IPv4-mapped private address
        ("::ffff:93.184.216.34", False, False),
        # Always blocked, even when listed:
        ("169.254.169.254", True, True),
        ("fe80::1%eth0", True, True),
        ("fd00:ec2::254", True, True),
        ("100.100.100.200", True, True),
        ("0.0.0.0", True, True),  # noqa: S104 - an address under test, not a bind
        ("224.0.0.1", True, True),
        ("255.255.255.255", True, True),
        ("::ffff:169.254.169.254", True, True),
        ("not-an-ip", True, True),
        # IPv6 transition addresses are checked as the IPv4 address they lead to (Phase 8).
        ("64:ff9b::a9fe:a9fe", True, True),  # NAT64 for 169.254.169.254, even when listed
        ("64:ff9b::7f00:1", False, True),  # NAT64 for 127.0.0.1
        ("64:ff9b::a00:5", False, True),  # NAT64 for 10.0.0.5
        ("64:ff9b::5db8:d822", False, False),  # NAT64 for a public address stays allowed
        ("::a9fe:a9fe", True, True),  # IPv4-compatible 169.254.169.254
        ("::7f00:1", False, True),  # IPv4-compatible 127.0.0.1
        ("2002:a9fe:a9fe::1", True, True),  # 6to4 for 169.254.169.254
        ("2001:0:4136:e378:8000:63bf:5601:5601", True, True),  # Teredo, client 169.254.169.254
        ("fec0::1", False, True),  # deprecated site-local
    ],
)
def test_address_checks(ip: str, listed: bool, blocked: bool) -> None:
    assert (PUBLIC.blocked_reason(ip, listed=listed) is not None) is blocked
