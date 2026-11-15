import pytest

from app.interfaces.web.client_ip import (
    load_trusted_proxy_networks,
    resolve_client_ip,
)


def test_no_trusted_proxy_configuration_ignores_forwarded_header() -> None:
    assert resolve_client_ip(
        peer_host="198.51.100.10",
        forwarded_for="203.0.113.99",
        trusted_proxies=(),
    ) == "198.51.100.10"


def test_forwarded_header_is_ignored_when_direct_peer_is_untrusted() -> None:
    trusted = load_trusted_proxy_networks("10.0.0.0/8")
    assert resolve_client_ip(
        peer_host="198.51.100.10",
        forwarded_for="203.0.113.99",
        trusted_proxies=trusted,
    ) == "198.51.100.10"


def test_resolves_client_from_a_trusted_proxy_chain() -> None:
    trusted = load_trusted_proxy_networks("10.0.0.0/8,192.0.2.0/24")
    assert resolve_client_ip(
        peer_host="10.0.0.10",
        forwarded_for="203.0.113.25, 192.0.2.8",
        trusted_proxies=trusted,
    ) == "203.0.113.25"


def test_ignores_spoofed_values_left_of_the_real_client() -> None:
    trusted = load_trusted_proxy_networks("10.0.0.0/8")
    assert resolve_client_ip(
        peer_host="10.0.0.10",
        forwarded_for="198.51.100.99, 203.0.113.25, 10.0.0.11",
        trusted_proxies=trusted,
    ) == "203.0.113.25"


@pytest.mark.parametrize(
    "forwarded_for",
    [
        "203.0.113.25, not-an-ip",
        "203.0.113.25, ",
        ",203.0.113.25",
        "unknown",
    ],
)
def test_malformed_forwarded_chain_falls_back_to_direct_peer(
    forwarded_for: str,
) -> None:
    trusted = load_trusted_proxy_networks("10.0.0.0/8")
    assert resolve_client_ip(
        peer_host="10.0.0.10",
        forwarded_for=forwarded_for,
        trusted_proxies=trusted,
    ) == "10.0.0.10"


def test_all_trusted_hops_fall_back_to_direct_peer() -> None:
    trusted = load_trusted_proxy_networks("10.0.0.0/8")
    assert resolve_client_ip(
        peer_host="10.0.0.10",
        forwarded_for="10.0.0.11, 10.0.0.12",
        trusted_proxies=trusted,
    ) == "10.0.0.10"


def test_ipv6_client_and_proxy_are_supported() -> None:
    trusted = load_trusted_proxy_networks("2001:db8:ffff::/48")
    assert resolve_client_ip(
        peer_host="2001:db8:ffff::10",
        forwarded_for="2001:db8::25",
        trusted_proxies=trusted,
    ) == "2001:db8::25"


def test_invalid_peer_address_is_reported_as_unknown() -> None:
    assert resolve_client_ip(
        peer_host="not-an-ip",
        forwarded_for="203.0.113.25",
        trusted_proxies=load_trusted_proxy_networks("10.0.0.0/8"),
    ) == "unknown"


def test_trusted_proxy_cidrs_are_parsed_and_normalized() -> None:
    networks = load_trusted_proxy_networks("10.0.0.7/8, 2001:db8::1")
    assert str(networks[0]) == "10.0.0.0/8"
    assert str(networks[1]) == "2001:db8::1/128"


@pytest.mark.parametrize("value", ["10.0.0.0/99", "not-a-network", "10.0.0.0/8,"])
def test_invalid_trusted_proxy_configuration_is_rejected(value: str) -> None:
    with pytest.raises(ValueError, match="TRUSTED_PROXY_CIDRS"):
        load_trusted_proxy_networks(value)



def test_forwarded_chain_with_too_many_hops_falls_back_to_peer() -> None:
    trusted = load_trusted_proxy_networks("10.0.0.0/8")
    chain = ", ".join("203.0.113.25" for _ in range(33))
    assert resolve_client_ip(
        peer_host="10.0.0.10",
        forwarded_for=chain,
        trusted_proxies=trusted,
    ) == "10.0.0.10"
