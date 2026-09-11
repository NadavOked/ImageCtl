"""#446: observing a room is not a state-machine event."""

from test_server_room import CLONER1, cloner_hello, room_server  # noqa: F401


def test_get_does_not_resume_a_closed_wave(room_server):
    client, ctx = room_server["deploy"], room_server["ctx"]
    response = client.post("/api/console/room", json={
        "image_id": "img_7f3a91", "target_drives": 3})
    assert response.status_code == 200
    before = dict(ctx.conn.execute("SELECT * FROM room_rounds").fetchone())
    ctx.store.close(before["wave_session_id"], "test")
    tables = ("room_rounds", "sessions", "session_members", "journal")
    def snapshot():
        return [[tuple(r) for r in ctx.conn.execute(f"SELECT * FROM {t}")]
                for t in tables]
    closed = snapshot()
    response = client.get("/api/console/room")
    assert response.status_code == 200
    assert snapshot() == closed, "GET advanced the room without a hello"
    assert response.json()["round"]["wave_number"] == before["wave_number"]
    cloner_hello(room_server["anon"], CLONER1, ["S1"])
    resumed = client.get("/api/console/room").json()["round"]
    assert resumed["wave_number"] == before["wave_number"] + 1
