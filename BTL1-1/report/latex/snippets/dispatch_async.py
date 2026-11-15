async def dispatch_async(self, head, body, client=None):
    """Coroutine version of :meth:`dispatch` for the asyncio transport."""
    req = self._prepare(head, body, client)
    loop = asyncio.get_running_loop()
    try:
        if not req.hook:
            return await loop.run_in_executor(None, self.response.build_response, req)
        if inspect.iscoroutinefunction(req.hook):
            result = await req.hook(req.headers, req.body)
        else:
            result = await loop.run_in_executor(None, req.hook, req.headers, req.body)
            if inspect.isawaitable(result):
                result = await result
    except Exception as exc:
        print("[HttpAdapter] Handler error on {} {}: {!r}".format(req.method, req.path, exc))
        return self.error_response(500, "Internal server error")
    return self.route_response(result)
