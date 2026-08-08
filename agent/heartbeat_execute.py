"""Execute DAT callbacks that maintain auth readiness and a 2-second heartbeat."""


def onStart():
    parent().Agent.refresh_auth(op("auth_table"))
    return


def onFrameStart(frame):
    del frame
    agent = parent().Agent
    if agent.connection_id and absTime.seconds - agent.last_heartbeat_at >= 2:
        op("socketio1").emit("heartbeat", agent.heartbeat_payload())
        agent.last_heartbeat_at = absTime.seconds
    return
