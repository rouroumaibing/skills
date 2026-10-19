def handle(req):
    try:
        return save(req)
    except TimeoutError:
        cleanup(req)
        return 504
