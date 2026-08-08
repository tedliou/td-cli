"""Canonical SocketIO DAT callbacks using ordinary JSON events."""


def onOpen(dat):
    dat.emit("register", parent().Agent.registration_payload())


def onReceiveEvent(dat, rowIndex, message, event):
    del rowIndex
    agent = parent().Agent
    if event == "registered":
        agent.connection_id = message["connection_id"]
        for result in agent.pending_results.values():
            result["connection_id"] = agent.connection_id
            dat.emit("request_result", result)
        dat.emit("results_replayed", agent.heartbeat_payload())
    elif event == "request_dispatch":
        envelope = {
            "request_id": message["request_id"],
            "instance_id": agent.instance_id,
            "connection_id": agent.connection_id,
        }
        result_event, result = agent.accept(message)
        if result_event == "request_result":
            dat.emit("request_accepted", envelope)
        else:
            result = {**envelope, **result}
        dat.emit(result_event, result)
    elif event == "result_recorded":
        agent.acknowledge_result(message["request_id"])


def onClose(dat, failure):
    del failure
    parent().Agent.connection_id = None
    parent().Agent.refresh_auth(op("auth_table"))
