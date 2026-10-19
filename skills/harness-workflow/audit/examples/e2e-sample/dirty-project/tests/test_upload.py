def test_timeout_returns_504():
    assert handle(slow_request()) == 504
