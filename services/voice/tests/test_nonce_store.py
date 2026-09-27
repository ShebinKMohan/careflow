from voice_agent.security.nonce_store import InMemoryNonceStore


async def test_inmemory_nonce_store_claims_each_nonce_once() -> None:
    clock = {"t": 0.0}
    store = InMemoryNonceStore(time_fn=lambda: clock["t"])

    assert await store.claim("n1", ttl_seconds=10) is True
    assert await store.claim("n1", ttl_seconds=10) is False  # replay within TTL
    assert await store.claim("n2", ttl_seconds=10) is True  # distinct nonce ok


async def test_inmemory_nonce_store_allows_reclaim_after_expiry() -> None:
    clock = {"t": 0.0}
    store = InMemoryNonceStore(time_fn=lambda: clock["t"])

    assert await store.claim("n1", ttl_seconds=10) is True
    clock["t"] = 11.0
    assert await store.claim("n1", ttl_seconds=10) is True
