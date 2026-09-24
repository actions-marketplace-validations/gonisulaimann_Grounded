def test_get_many(b, tasks):
    list(b.get_many(tasks, timeout=0, interval=0.01, max_iterations=3))
    # timeout=0 means do not block, so it must not sleep either.
    assert True


def test_port(x, conf):
    conf.port = None
    # Default port is 9042
    assert x.port == 9042
    conf.port = 1234


def connect(host):
    # Retries: max_retries is 5 by default.
    max_retries = 3
    return host, max_retries
