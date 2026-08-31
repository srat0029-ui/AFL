from app.api_platform.keys import generate_api_key, hash_key, key_prefix


def test_generate_api_key_has_expected_prefix_and_length():
    key = generate_api_key()
    assert key.startswith("afl_")
    assert len(key) > 32

def test_generate_api_key_is_random():
    assert generate_api_key() != generate_api_key()


def test_hash_key_is_deterministic():
    key = generate_api_key()
    assert hash_key(key) == hash_key(key)


def test_hash_key_differs_for_different_keys():
    assert hash_key(generate_api_key()) != hash_key(generate_api_key())


def test_hash_key_is_sha256_hexdigest_shape():
    digest = hash_key("afl_test")
    assert len(digest) == 64
    assert all(c in "0123456789abcdef" for c in digest)


def test_key_prefix_is_a_prefix_of_the_raw_key():
    key = generate_api_key()
    prefix = key_prefix(key)
    assert key.startswith(prefix)
    assert len(prefix) < len(key)
