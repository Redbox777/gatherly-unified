"""Resolve client IPs without trusting spoofable forwarding headers."""

from ipaddress import (
    IPv4Address,
    IPv4Network,
    IPv6Address,
    IPv6Network,
    ip_address,
    ip_network,
)

IPAddress = IPv4Address | IPv6Address
IPNetwork = IPv4Network | IPv6Network


def load_trusted_proxy_networks(value: str | None) -> tuple[IPNetwork, ...]:
    """Parse comma-separated trusted proxy CIDRs from environment configuration."""
    if value is None or not value.strip():
        return ()

    entries = value.split(",")
    networks: list[IPNetwork] = []
    for raw_entry in entries:
        entry = raw_entry.strip()
        if not entry:
            raise ValueError("TRUSTED_PROXY_CIDRS contains an empty entry")
        try:
            network = ip_network(entry, strict=False)
        except ValueError as error:
            raise ValueError(
                f"TRUSTED_PROXY_CIDRS contains an invalid network: {entry}"
            ) from error
        if network.prefixlen == 0:
            raise ValueError("TRUSTED_PROXY_CIDRS must not trust every IP address")
        networks.append(network)
    return tuple(networks)


def _is_trusted(address: IPAddress, networks: tuple[IPNetwork, ...]) -> bool:
    return any(
        address.version == network.version and address in network
        for network in networks
    )


def resolve_client_ip(
    *,
    peer_host: str | None,
    forwarded_for: str | None,
    trusted_proxies: tuple[IPNetwork, ...],
) -> str:
    """Resolve the first untrusted hop from right to left in a trusted proxy chain.

    X-Forwarded-For is ignored unless the direct TCP peer is explicitly trusted.
    Every address in the header must be a valid IP literal; malformed chains fall
    back to the direct peer instead of partially trusting attacker-controlled data.
    """
    if not peer_host:
        return "unknown"
    try:
        peer = ip_address(peer_host.strip())
    except ValueError:
        return "unknown"

    if not _is_trusted(peer, trusted_proxies) or not forwarded_for:
        return str(peer)

    raw_addresses = forwarded_for.split(",")
    if len(raw_addresses) > 32 or any(not item.strip() for item in raw_addresses):
        return str(peer)

    try:
        chain = [ip_address(item.strip()) for item in raw_addresses]
    except ValueError:
        return str(peer)

    for candidate in reversed(chain):
        if _is_trusted(candidate, trusted_proxies):
            continue
        return str(candidate)

    # A chain containing only trusted proxies does not identify an end client.
    return str(peer)
