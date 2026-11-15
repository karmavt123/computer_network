def resolve_routing_policy(hostname, routes):
    """
    Handles an routing policy to return the ordered list of backends to try.

    The first element is chosen by the distribution policy; the remaining
    backends of the pool follow as fail-over candidates.

    :params hostname (str): Host header of the request.
    :params routes (dict): ``{host: (proxy_pass or [proxy_pass...], policy, headers)}``.
    :rtype list: ``[(host, port), ...]``; empty if the host is unknown.
    """
    key, entry = _lookup(hostname, routes)
    if entry is None:
        return []
    backends = _backends_of(entry)
    if not backends:
        return []
    policy = entry[1] if len(entry) > 1 else "round-robin"
    with _lock:
        if policy == "random":
            first = random.randrange(len(backends))
        elif policy == "least-conn":
            first = min(range(len(backends)),
                        key=lambda i: _active.get(backends[i], 0))
        else:                                   # round-robin
            counter = _counters.setdefault(key, itertools.count())
            first = next(counter) % len(backends)
    ordered = backends[first:] + backends[:first]
    result = []
    for item in ordered:
        host, _, port = item.partition(":")
        result.append((host, int(port or 80)))
    return result
