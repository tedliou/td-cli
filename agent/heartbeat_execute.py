"""Independent-time Agent scheduler using TouchDesigner's official run delay reference."""


def startScheduler():
    generation = parent().ext.Agent.start_heartbeat()
    scheduleTick(generation)


def stopScheduler():
    parent().ext.Agent.stop_heartbeat()


def scheduleTick(generation):
    run(
        schedulerTick,
        generation,
        delayMilliSeconds=2000,
        delayRef=op.TDResources,
    )


def schedulerTick(generation):
    agent = parent().ext.Agent
    if not agent.heartbeat_active(generation):
        return
    if agent.connection_id:
        agent.mark_heartbeat()
        op("socketio1").emit("heartbeat", data=agent.heartbeat_payload())
    scheduleTick(generation)
