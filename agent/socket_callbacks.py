"""Canonical SocketIO DAT callbacks using ordinary JSON events."""


def onOpen(dat):
    dat.emit("register", parent().ext.Agent.registration_payload())


def onReceiveEvent(dat, rowIndex, message, event):
    del rowIndex
    agent = parent().ext.Agent
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
        if agent.draining and not agent.pending_results:
            finishDraining(dat)
    elif event == "daemon_draining":
        agent.begin_draining()
        dat.emit("heartbeat", agent.heartbeat_payload())
        if not agent.pending_results:
            finishDraining(dat)
        else:
            run(
                "op('socket_callbacks').module.finishDraining(args[0])",
                dat,
                delayMilliSeconds=int(float(message["deadline_seconds"]) * 1000),
                fromOP=me,
            )


def onClose(dat, failure):
    del failure
    parent().ext.Agent.connection_id = None
    parent().ext.Agent.refresh_auth(op("auth_table"))


def finishDraining(dat):
    agent = parent().ext.Agent
    if agent.connection_id:
        dat.emit("unregister", agent.heartbeat_payload())
        dat.par.active = False
