"""Canonical SocketIO DAT callbacks for the Protocol v2 execution handshake."""


def onOpen(dat):
    agent = parent().ext.Agent
    agent.begin_socket_generation()
    agent.end_draining()
    op("heartbeat_execute").module.startScheduler()
    agent.clear_auth(op("auth_table"))
    dat.emit("register", data=agent.registration_payload())


def onReceiveEvent(dat, rowIndex, message, event):
    del rowIndex
    agent = parent().ext.Agent
    if event == "registered":
        agent.rebind_connection(message["connection_id"])
        records = []
        for record in agent.synchronization_records():
            if record["phase"] == "outcome":
                emitOutcome(
                    dat,
                    {key: value for key, value in record.items() if key != "phase"},
                    force_chunks=True,
                )
            else:
                records.append(record)
        dat.emit(
            "execution_sync",
            data={**agent.heartbeat_payload(), "records": records},
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
            resumeAfterDraining,
            dat,
            delayMilliSeconds=deadline_milliseconds + 500,
            delayRef=op.TDResources,
        )
        if agent.retention_snapshot()["record_count"] == 0:
            finishDraining(dat)
        else:
            run(
                finishDraining,
                dat,
                delayMilliSeconds=deadline_milliseconds,
                delayRef=op.TDResources,
            )


def executeScheduled(dat, request_id, execution_id):
    outcome = parent().ext.Agent.execute_authorized(request_id, execution_id)
    if outcome is not None:
        emitOutcome(dat, outcome)


def emitOutcome(dat, outcome, force_chunks=False):
    chunks = parent().ext.Agent.outcome_chunks(outcome)
    if len(chunks) == 1 and not force_chunks:
        dat.emit("request_outcome", data=outcome)
        return
    for chunk in chunks:
        dat.emit("request_outcome_chunk", data=chunk)


def scheduleExecution(dat, request_id, execution_id):
    run(
        executeScheduled,
        dat,
        request_id,
        execution_id,
        delayMilliSeconds=1,
        delayRef=op.TDResources,
    )


def onClose(dat, failure):
    del dat, failure
    agent = parent().ext.Agent
    if not agent.end_socket_generation():
        return
    op("heartbeat_execute").module.stopScheduler()
    if agent.runtime_active:
        agent.refresh_auth(op("auth_table"))


def finishDraining(dat):
    agent = parent().ext.Agent
    if agent.connection_id:
        dat.emit("unregister", data=agent.heartbeat_payload())
        dat.par.active = False


def resumeAfterDraining(dat):
    dat.par.active = True
