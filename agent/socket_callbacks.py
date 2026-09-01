"""Canonical SocketIO DAT callbacks for the Protocol v2 execution handshake."""


def onOpen(dat):
    agent = parent().ext.Agent
    agent.end_draining()
    dat.emit("register", data=agent.registration_payload())


def onReceiveEvent(dat, rowIndex, message, event):
    del rowIndex
    agent = parent().ext.Agent
    if event == "registered":
        agent.connection_id = message["connection_id"]
        dat.emit(
            "execution_sync",
            data={**agent.heartbeat_payload(), "records": agent.synchronization_records()},
        )
        for request_id, execution_id in agent.authorized_records():
            scheduleExecution(dat, request_id, execution_id)
    elif event == "request_dispatch":
        result_event, payload = agent.reserve(message)
        dat.emit(result_event, data=payload)
    elif event == "request_execute":
        if agent.authorize(message):
            scheduleExecution(dat, message["request_id"], message["execution_id"])
    elif event == "outcome_recorded":
        agent.acknowledge_outcome(message["request_id"], message.get("execution_id"))
        if agent.draining and agent.retention_snapshot()["record_count"] == 0:
            finishDraining(dat)
    elif event == "record_release":
        agent.release_record(message["request_id"])
    elif event == "daemon_draining":
        agent.begin_draining()
        dat.emit("heartbeat", data=agent.heartbeat_payload())
        deadline_milliseconds = int(float(message["deadline_seconds"]) * 1000)
        run(
            "op('socket_callbacks').module.resumeAfterDraining(args[0])",
            dat,
            delayMilliSeconds=deadline_milliseconds + 500,
            delayRef=op.TDResources,
        )
        if agent.retention_snapshot()["record_count"] == 0:
            finishDraining(dat)
        else:
            run(
                "op('socket_callbacks').module.finishDraining(args[0])",
                dat,
                delayMilliSeconds=deadline_milliseconds,
                delayRef=op.TDResources,
            )


def executeScheduled(dat, request_id, execution_id):
    outcome = parent().ext.Agent.execute_authorized(request_id, execution_id)
    if outcome is not None:
        dat.emit("request_outcome", data=outcome)


def scheduleExecution(dat, request_id, execution_id):
    run(
        "op('socket_callbacks').module.executeScheduled(args[0], args[1], args[2])",
        dat,
        request_id,
        execution_id,
        delayMilliSeconds=1,
        delayRef=op.TDResources,
    )


def onClose(dat, failure):
    del dat, failure
    parent().ext.Agent.connection_id = None
    parent().ext.Agent.refresh_auth(op("auth_table"))


def finishDraining(dat):
    agent = parent().ext.Agent
    if agent.connection_id:
        dat.emit("unregister", data=agent.heartbeat_payload())
        dat.par.active = False


def resumeAfterDraining(dat):
    dat.par.active = True
