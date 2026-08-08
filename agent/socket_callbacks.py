"""Canonical SocketIO DAT callbacks; ordinary JSON events, never acknowledgements."""


def onConnect(dat):
    dat.send("register", parent().Agent.registration_payload())


def onReceiveEvent(dat, event, data):
    agent = parent().Agent
    if event == "registered":
        agent.connection_id = data["connection_id"]
        for result in agent.pending_results.values():
            result["connection_id"] = agent.connection_id
            dat.send("request_result", result)
    elif event == "request_dispatch":
        envelope = {
            "request_id": data["request_id"],
            "instance_id": agent.instance_id,
            "connection_id": agent.connection_id,
        }
        dat.send("request_accepted", envelope)
        result_event, result = agent.accept(data)
        dat.send(result_event, result)
    elif event == "result_recorded":
        agent.acknowledge_result(data["request_id"])


def onDisconnect(dat):
    parent().Agent.connection_id = None


def onHeartbeat(dat):
    if parent().Agent.connection_id:
        dat.send("heartbeat", parent().Agent.heartbeat_payload())
