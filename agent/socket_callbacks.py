"""Canonical SocketIO DAT callbacks; ordinary JSON events, never acknowledgements."""


def onConnect(dat):
    dat.send("register", parent().Agent.registration_payload())


def onReceiveEvent(dat, event, data):
    if event == "registered":
        parent().Agent.connection_id = data["connection_id"]
    elif event == "result_recorded":
        parent().Agent.pending_results.pop(data["request_id"], None)
    return


def onDisconnect(dat):
    parent().Agent.connection_id = None
    return
