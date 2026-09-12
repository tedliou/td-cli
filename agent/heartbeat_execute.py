"""Independent-time Agent scheduler using TouchDesigner's official run delay reference."""


def startScheduler():
    component = parent()
    generation = component.ext.Agent.start_heartbeat()
    scheduleTick(generation, component)


def stopScheduler():
    parent().ext.Agent.stop_heartbeat()


def scheduleTick(generation, component):
    run(
        schedulerTick,
        generation,
        component,
        delayMilliSeconds=2000,
        delayRef=op.TDResources,
    )


def schedulerTick(generation, component):
    agent = component.ext.Agent
    if not agent.heartbeat_active(generation):
        return
    agent.maintain_connection()
    if agent.connection_id:
        agent.mark_heartbeat()
        component.op("socketio1").emit("heartbeat", data=agent.heartbeat_payload())
    scheduleTick(generation, component)
