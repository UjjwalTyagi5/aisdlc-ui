# agentic_app/config/connection_manager.py
from typing import List, Dict
from fastapi import WebSocket
from uuid import uuid4
import asyncio
import json


class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self.agents: Dict[str, Dict] = {}
        self.sessions: Dict[str, Dict] = {}
        # session_id → list of websockets connected for that session
        self._session_connections: Dict[str, List[WebSocket]] = {}
        # id(websocket) → session_id (so disconnect can clean up)
        self._ws_to_session: Dict[int, str] = {}

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def register_session(self, websocket: WebSocket, session_id: str) -> None:
        """Associate a connected websocket with a session_id.

        Called as soon as session_id is known (first message from client).
        Subsequent broadcast() calls with a matching session_id will only
        reach websockets registered under that session.

        A NEW SOCKET FOR A SESSION MEANS A NEW TURN, which is why any cancellation
        against it is cleared here. The chat BFF opens one WebSocket per turn, so this
        runs exactly once per turn — and without it, stopping one turn would cancel every
        later turn on the same conversation until the TTL expired. That is a far worse
        bug than the one cancellation exists to fix: a Stop button that silently breaks
        the chat.
        """
        from config.turn_cancellation import clear as _clear_cancellation  # noqa: PLC0415

        _clear_cancellation(session_id)

        old = self._ws_to_session.get(id(websocket))
        if old and old != session_id:
            self._session_connections[old] = [
                ws for ws in self._session_connections.get(old, [])
                if ws is not websocket
            ]
        self._ws_to_session[id(websocket)] = session_id
        self._session_connections.setdefault(session_id, [])
        if websocket not in self._session_connections[session_id]:
            self._session_connections[session_id].append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        ws_key = id(websocket)
        session_id = self._ws_to_session.pop(ws_key, None)
        if session_id and session_id in self._session_connections:
            self._session_connections[session_id] = [
                ws for ws in self._session_connections[session_id]
                if ws is not websocket
            ]

    async def send_personal_message(self, message: str, websocket: WebSocket):
        try:
            await websocket.send_text(message)
        except Exception as e:
            print(f"Error sending personal message: {e}")

    async def broadcast(self, message: dict):
        """Send message only to websockets registered under message['session_id'].

        Falls back to broadcasting to ALL connections only when session_id is
        absent from the message (e.g. agents_cleared, legacy paths). A session_id
        that is present but unregistered — one that names nobody — sends to NOBODY,
        and never to everybody.

        THAT DISTINCTION WAS THE BUG, and this docstring is where it hid. The
        condition used to be `if session_id and session_id in self._session_connections`
        with a bare `else`, so the fallback fired in two cases: session_id absent (as
        documented) and session_id present-but-unregistered (not documented, and a
        cross-session leak).

        It was not hypothetical. NOTHING registers a socket under an orchestrator2 run
        id — `orchestrator2/ws.py` and `dispatch.py` never call `register_session` —
        so every orchestrator2 run took the undocumented branch, and a document
        generated there was streamed onto whatever unrelated sockets were open on the
        process. 79 call sites reach this method; fixing them individually would have
        left the 80th to reintroduce it.

        Silence is the right failure. Both behaviours lose the message for its
        intended reader — that socket is not connected either way — but only one of
        them hands it to someone else.
        """
        session_id = message.get("session_id")

        # STOP LANDS HERE, and this is the only place it could land cheaply. An agent
        # notices a closed socket at its next `receive_text()`, which does not run until
        # the turn it is inside has finished — so closing the connection made the chat go
        # quiet while the agent kept generating and kept running tools. Every agent's
        # output already funnels through this method (around sixty call sites), so
        # raising here unwinds whichever loop is producing it without teaching nine
        # separate stream loops a new convention.
        if session_id:
            from config.turn_cancellation import TurnCancelled, is_cancelled  # noqa: PLC0415

            if is_cancelled(session_id):
                raise TurnCancelled(session_id)

        if session_id:
            # Addressed to a session: its own sockets, or none. `.get` rather than a
            # membership test, so a session whose sockets have all disconnected is
            # treated the same as one that never registered — both name nobody.
            targets = list(self._session_connections.get(session_id, ()))
        else:
            # Addressed to no session at all. `agents_cleared` and the legacy paths
            # genuinely mean everyone, which is why this branch survives.
            targets = list(self.active_connections)

        if not targets:
            return

        message_str = json.dumps(message)
        disconnected = []
        for connection in targets:
            try:
                await connection.send_text(message_str)
            except Exception as e:
                print(f"Error broadcasting to connection: {e}")
                disconnected.append(connection)
        for conn in disconnected:
            self.disconnect(conn)

    async def broadcast_to_session(self, message: dict):
        """Send message ONLY to websockets registered under message['session_id'].

        Unlike broadcast(), this never falls back to all active connections —
        if session_id is missing or has no registered connections, nothing is
        sent to anyone. Use this for payloads (e.g. file_diff) that can carry
        sensitive per-session content and must never fan out cross-tenant.
        """
        session_id = message.get("session_id")
        if not session_id:
            return
        targets = list(self._session_connections.get(session_id, []))
        if not targets:
            return

        message_str = json.dumps(message)
        disconnected = []
        for connection in targets:
            try:
                await connection.send_text(message_str)
            except Exception as e:
                print(f"Error broadcasting to session connection: {e}")
                disconnected.append(connection)
        for conn in disconnected:
            self.disconnect(conn)

    async def add_agent(self, agent_data: dict):
        agent_id = str(uuid4())
        self.agents[agent_id] = {
            **agent_data,
            "id": agent_id,
            "status": "joining",
            "progress": 0
        }
        await self.broadcast({
            "type": "agent_joining",
            "agent": self.agents[agent_id]
        })
        for progress in [25, 50, 75, 100]:
            await asyncio.sleep(0.1)
            if agent_id in self.agents:
                self.agents[agent_id]["progress"] = progress
                await self.broadcast({
                    "type": "agent_progress",
                    "agent_id": agent_id,
                    "progress": progress
                })
        if agent_id in self.agents:
            self.agents[agent_id]["status"] = "active"
            await self.broadcast({
                "type": "agent_active",
                "agent_id": agent_id,
                "response_time": 1.2
            })
        return agent_id

    async def send_agent_response(self, agent_name: str, message: str, session_id: str):
        await self.broadcast({
            "type": "agent_response",
            "agent_name": agent_name,
            "message": message,
            "session_id": session_id
        })

    async def send_file_processing_update(self, session_id: str, file_names: List[str]):
        await self.broadcast({
            "type": "file_processing",
            "session_id": session_id,
            "files": file_names,
            "message": f"Processing {len(file_names)} file(s)..."
        })

    async def send_session_update(self, session_id: str, status: str, message: str):
        await self.broadcast({
            "type": "session_update",
            "session_id": session_id,
            "status": status,
            "message": message
        })

    async def send_agent_completion(self, session_id: str):
        await self.broadcast({
            "type": "agent_completed",
            "session_id": session_id,
            "status": "completed"
        })

    async def clear_agents(self):
        self.agents.clear()
        await self.broadcast({"type": "agents_cleared"})


# shared singleton
manager = ConnectionManager()
