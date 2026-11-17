"""RED tests for port-agnostic local-preview grants in LocalPreviewPolicy.

Spec (P2): grants are keyed by (scheme, host, port|None); port None means
"all ports". grant(raw, all_ports=True) stores the wildcard grant, and
allows(raw) accepts any non-forbidden loopback port while the grant is
unexpired.
"""

from crabagent.core.computer.url_policy import LocalPreviewPolicy


def test_all_ports_grant_allows_any_loopback_port():
    p = LocalPreviewPolicy()
    p.grant("http://localhost", all_ports=True)

    assert p.allows("http://localhost:5173/processamento")
    assert p.allows("http://localhost:3000")


def test_all_ports_grant_excludes_forbidden_ports():
    p = LocalPreviewPolicy(forbidden_ports=lambda: [5173])
    p.grant("http://localhost", all_ports=True)

    assert not p.allows("http://localhost:5173")
    assert p.allows("http://localhost:3000")


def test_all_ports_grant_rejects_non_loopback():
    p = LocalPreviewPolicy()
    p.grant("http://localhost", all_ports=True)

    assert not p.allows("http://192.168.1.5")
    assert not p.allows("https://example.com")


def test_exact_grant_still_port_scoped():
    p = LocalPreviewPolicy()
    p.grant("http://localhost:5173")

    assert p.allows("http://localhost:5173")
    assert not p.allows("http://localhost:3000")


def test_all_ports_grant_expires():
    clock = [0.0]
    p = LocalPreviewPolicy(ttl_ms=1000, now=lambda: clock[0])
    p.grant("http://localhost", all_ports=True)
    assert p.allows("http://localhost:5173")  # granted while fresh

    clock[0] = 5.0
    assert not p.allows("http://localhost:5173")
