import stat

from vibecontext.config import ensure_home, load_api_token, load_settings


def mode(path):
    return stat.S_IMODE(path.stat().st_mode)


def test_first_run_creates_private_secrets(paths):
    assert mode(paths.root) == 0o700
    assert mode(paths.env) == 0o600
    assert mode(paths.secrets) == 0o600
    assert len(load_api_token(paths)) >= 40
    assert len(load_settings(paths).qdrant_api_key) >= 40


def test_second_run_keeps_existing_secrets(paths):
    token = load_api_token(paths)
    qdrant_key = load_settings(paths).qdrant_api_key
    ensure_home(paths)
    assert load_api_token(paths) == token
    assert load_settings(paths).qdrant_api_key == qdrant_key
