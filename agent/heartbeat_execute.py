"""Independent-time Agent scheduler using TouchDesigner's official run delay reference."""


def onStart():
    parent().ext.Agent.refresh_auth(op("auth_table"))
    scheduleTick()
    return


def scheduleTick():
    run(
        "op('heartbeat_execute').module.schedulerTick()",
        delayMilliSeconds=2000,
        delayRef=op.TDResources,
    )


def schedulerTick():
    agent = parent().ext.Agent
    if agent.connection_id:
        op("socketio1").emit("heartbeat", data=agent.heartbeat_payload())
    scheduleTick()
