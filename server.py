from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import httpx
import uvicorn
import json
import asyncio
from typing import Dict, List
from data_validator import validate_level_data
from setup_validator import validate_player_setup


app = FastAPI()

# --- ДОБАВЬТЕ ЭТОТ БЛОК ---
@app.get("/")
async def health_check():
    return "Server is running"
# --- CONFIGURATION ---
TARGET_APP_ID = 480  
STEAM_WEB_API_KEY = "6AB9786E0AD6BCFF371672A8A8586A26" 
USE_STEAM_VERIFICATION = False 
# ---------------------

class Room:
    def __init__(self, room_id, host_id):
        self.id = room_id
        self.host = host_id
        self.name = f"{host_id.split(':')[1]}'s Room" if ":" in host_id else f"{host_id}'s Room"
        self.players = [] # List of steam_ids
        self.chat_history = []
        self.current_level_data = None
        self.player_setups = {} # user_id -> setup_data
        self.match_results = {} # reporter -> {opponent: result}
        self.scores = {}        # user_id -> points
        self.status = "OPEN"
        self.max_players = 2 # Default limit
        self.quick_match_allowed = True # Default allowed
        self.equip_timer = 15 # Default 15 minutes
        self.level_name = "None"
        self.is_random = False
        self.mercs_min = 3
        self.mercs_max = 7
        self.char_min = 4
        self.char_max = 12
        self.money_min = 500
        self.money_max = 2000
        self.levels_min = 5
        self.levels_max = 20
        self.char_pool_size = 16
        self.char_variety_min = -100
        self.char_variety_max = 100
        self.equip_variety_min = -100
        self.equip_variety_max = 100
        self.item_min = 30
        self.item_max = 70
        self.setup_character_count = 0
        self.setup_equipment_count = 0
        self.time_remaining = 0
        self.timer_task = None
        print(f"[Server] Created Room {room_id} with status {self.status}")

    def add_player(self, user_id):
        if user_id not in self.players:
            self.players.append(user_id)
            
    def remove_player(self, user_id):
        if user_id in self.players:
            self.players.remove(user_id)
            # Clean up game data if present
            if user_id in self.player_setups: del self.player_setups[user_id]
            if user_id in self.scores: del self.scores[user_id]
            if user_id in self.match_results: del self.match_results[user_id]
            return len(self.players) == 0 # Return True if empty
        return False

class RoomManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        self.lobby_chat: list = []  
        self.rooms: Dict[str, Room] = {}
        self.player_room_map: Dict[str, str] = {} # user_id -> room_id

    async def connect(self, websocket: WebSocket, user_id: str):
        self.active_connections[user_id] = websocket
        # Send Lobby Chat History
        for msg in self.lobby_chat:
            await self.send_json(user_id, {"type": "CHAT", "context": "LOBBY", "txt": msg})

    async def disconnect(self, user_id: str):
        if user_id in self.active_connections:
            del self.active_connections[user_id]
        
        # Leave room if in one
        await self.leave_room(user_id)

    def active_room(self, user_id): # Keep sync if possible, used in broadcast
        rid = self.player_room_map.get(user_id)
        if rid: return self.rooms.get(rid)
        return None

    async def create_room(self, user_id):
        # Leave current first
        await self.leave_room(user_id)
        
        room_id = f"room_{len(self.rooms)+1}_{int(asyncio.get_event_loop().time())}"
        room = Room(room_id, user_id)
        room.status = "OPEN" # Force set
        room.add_player(user_id)
        self.rooms[room_id] = room
        self.player_room_map[user_id] = room_id
        await self.broadcast_room_state(room_id)
        return room

    async def join_room(self, user_id, room_id):
        await self.leave_room(user_id)
        if room_id in self.rooms:
            room = self.rooms[room_id]
            if len(room.players) >= room.max_players:
                return None # Room Full
            
            room.add_player(user_id)
            self.player_room_map[user_id] = room_id
            await self.broadcast_room_state(room_id)
            return room
        return None

    async def leave_room(self, user_id):
        if user_id in self.player_room_map:
            rid = self.player_room_map[user_id]
            if rid in self.rooms:
                room = self.rooms[rid]
                
                # Check if host - only disband if game hasn't started
                # If game is PLAYING, host leaving should be treated as a normal player drop
                should_disband = (room.host == user_id and getattr(room, 'status', 'OPEN') == "OPEN")
                
                if should_disband:
                    print(f"[Server] Host {user_id} left OPEN room {rid}. Disbanding.")
                    # Disband room
                    players_to_kick = list(room.players) # Copy list
                    for pid in players_to_kick:
                        # Remove from map
                        if pid in self.player_room_map:
                            del self.player_room_map[pid]
                        # Notify
                        await self.send_json(pid, {"type": "LEFT_ROOM"})
                        if pid != user_id:
                            await self.send_json(pid, {"type": "SYSTEM", "txt": "Room disbanded by host."})
                    # Delete room
                    del self.rooms[rid]
                else:
                    # Normal leave (or Host leaving during PLAYING)
                    if room.host == user_id:
                        print(f"[Server] Host {user_id} left PLAYING room {rid}. Game continues.")
                    
                    is_empty = room.remove_player(user_id)
                    del self.player_room_map[user_id]
                    if is_empty:
                        del self.rooms[rid]
                    else:
                        await self.broadcast_room_state(rid)
                        
                        # Handle disconnection during game setup (or play)
                        if getattr(room, 'status', 'OPEN') == "PLAYING":
                            # If we were waiting for setups, check if everyone remaining is ready
                            if len(room.player_setups) == len(room.players) and len(room.players) > 0:
                                payload = {
                                    "type": "ALL_SETUPS_READY",
                                    "setups": room.player_setups
                                }
                                for uid in room.players:
                                    await self.send_json(uid, payload)
                            
                            # Broadcast tournament update effectively removing the leaver from scores
                            if len(room.players) > 0:
                                await self.broadcast_tournament_update(room)
                        
        # If user is still connected (moved to lobby), send them lobby history as they missed it while in room
        if user_id in self.active_connections:
             for msg in self.lobby_chat:
                await self.send_json(user_id, {"type": "CHAT", "context": "LOBBY", "txt": msg})

    async def broadcast_lobby(self, message: str, sender_id: str):
        formatted = f"[{sender_id}]: {message}"
        self.lobby_chat.append(formatted)
        if len(self.lobby_chat) > 20: self.lobby_chat.pop(0)
        
        evt = {"type": "CHAT", "context": "LOBBY", "txt": formatted}
        
        # Broadcast to EVERYONE (Lobby + Rooms? Or just Lobby?)
        # Usually global chat is visible everywhere or just lobby. 
        # Requirement: "Private room has it's own chat visible ONLY for joined players"
        # Implication: Lobby chat might not be visible in room? 
        # Let's broadcast to everyone for now, OR restrict to those not in room.
        # Let's restrict to those NOT in a room (player_room_map check).
        
        for uid, ws in self.active_connections.items():
            if uid not in self.player_room_map: 
                await self.send_json(uid, evt)

    async def broadcast_room(self, message: str, sender_id: str):
        room = self.active_room(sender_id)
        if not room: return
        
        formatted = f"[{sender_id}]: {message}"
        room.chat_history.append(formatted)
        if len(room.chat_history) > 20: room.chat_history.pop(0)
        
        evt = {"type": "CHAT", "context": "ROOM", "txt": formatted}
        for uid in room.players:
            await self.send_json(uid, evt)

    async def broadcast_room_state(self, room_id: str):
        if room_id in self.rooms:
            room = self.rooms[room_id]
            evt = {
                "type": "ROOM_STATE", 
                "players": room.players,
                "max_players": room.max_players,
                "quick_match_allowed": room.quick_match_allowed,
                "equip_timer": room.equip_timer,
                "level_name": room.level_name,
                "is_random": room.is_random,
                "mercs_min": room.mercs_min,
                "mercs_max": room.mercs_max,
                "char_min": room.char_min,
                "char_max": room.char_max,
                "money_min": room.money_min,
                "money_max": room.money_max,
                "levels_min": room.levels_min,
                "levels_max": room.levels_max,
                "char_pool_size": room.char_pool_size,
                "char_variety_min": room.char_variety_min,
                "char_variety_max": room.char_variety_max,
                "equip_variety_min": room.equip_variety_min,
                "equip_variety_max": room.equip_variety_max,
                "item_min": room.item_min,
                "item_max": room.item_max,
                "setup_character_count": room.setup_character_count,
                "setup_equipment_count": room.setup_equipment_count
            }
            for uid in room.players:
                await self.send_json(uid, evt)

    async def broadcast_tournament_update(self, room):
        # Recalculate Scores from scratch using ONLY verified pairs
        temp_scores = {uid: 0 for uid in room.players}
        
        processed_pairs = set()
        for p1 in room.players:
            for p2 in room.players:
                if p1 == p2: continue
                
                pair_key = tuple(sorted((p1, p2)))
                if pair_key in processed_pairs: continue
                
                res1 = room.match_results.get(p1, {}).get(p2) # What P1 says about its game vs P2
                res2 = room.match_results.get(p2, {}).get(p1) # What P2 says about its game vs P1
                
                if res1 and res2:
                    processed_pairs.add(pair_key)
                    
                    # Verification
                    if res1 == "win" and res2 == "loss":
                        temp_scores[p1] += 2
                    elif res1 == "loss" and res2 == "win":
                        temp_scores[p2] += 2
                    elif res1 == "tie" and res2 == "tie":
                        temp_scores[p1] += 1
                        temp_scores[p2] += 1
                    else:
                        print(f"[Server] DISPUTE detected between {p1} and {p2}: {res1} vs {res2}")
                        # Disputes award 0 points for safety
        
        # Update room scores with currently verified results
        room.scores = temp_scores
        num_players = len(room.players)
        total_expected_pairs = (num_players * (num_players - 1)) // 2
        # Avoid division by zero if num_players is 0 or 1 (total_expected_pairs = 0)
        
        is_final = (len(processed_pairs) >= total_expected_pairs)
        
        # Broadcast EVERY update including raw results so client can build the full matrix
        payload = {
            "type": "TOURNAMENT_UPDATE",
            "scores": room.scores,
            "results": room.match_results,
            "verified_count": len(processed_pairs),
            "total_pairs": total_expected_pairs,
            "is_final": is_final
        }
        for uid in room.players:
            await self.send_json(uid, payload)

    async def _room_timer_logic(self, room_id: str):
        """Background task for handling room equipment timer."""
        print(f"[Server] Starting timer logic for room {room_id}")
        try:
            while room_id in self.rooms:
                room = self.rooms[room_id]
                if room.status != "PLAYING":
                    break
                
                if room.time_remaining <= 0:
                    # Timer expired! Kick players who haven't submitted
                    print(f"[Server] Timer expired for room {room_id}. Checking setups.")
                    to_kick = [pid for pid in room.players if pid not in room.player_setups]
                    
                    for pid in to_kick:
                        print(f"[Server] Kicking {pid} from room {room_id} due to timer expiry.")
                        # Inform player first
                        await self.send_json(pid, {"type": "SYSTEM", "txt": "Kicked/Equipment timer expired."})
                        await self.send_json(pid, {"type": "LEFT_ROOM"})
                        await self.leave_room(pid)
                    
                    # If we still have players, and everyone who is left has submitted, we can proceed
                    # leave_room might have triggered ALL_SETUPS_READY if the leaver was the last one missing.
                    # But if multiple were kicked, we might need a nudge.
                    if room_id in self.rooms:
                        room = self.rooms[room_id]
                        if len(room.players) > 0 and len(room.player_setups) == len(room.players):
                             payload = {
                                "type": "ALL_SETUPS_READY",
                                "setups": room.player_setups
                            }
                             for uid in room.players:
                                await self.send_json(uid, payload)
                            
                             if room.timer_task:
                                room.timer_task.cancel()
                                room.timer_task = None
                    
                    break # Timer task ends
                
                # Sleep and sync every 60s
                if room.time_remaining % 60 == 0:
                    sync_payload = {"type": "TIMER_SYNC", "time_remaining": room.time_remaining}
                    for uid in room.players:
                        await self.send_json(uid, sync_payload)
                
                await asyncio.sleep(1)
                room.time_remaining -= 1
                
        except asyncio.CancelledError:
            print(f"[Server] Timer task cancelled for room {room_id}")
        except Exception as e:
            print(f"[Server] Error in timer task: {e}")

    async def send_json(self, user_id, data):
        ws = self.active_connections.get(user_id)
        if ws:
            try: await ws.send_text(json.dumps(data))
            except: pass

    def get_room_list(self):
        visible = [r for r in self.rooms.values() if getattr(r, 'status', 'OPEN') == "OPEN"]
        # print(f"[Server] Serving room list: {len(visible)} visible out of {len(self.rooms)} total")
        return [{"id": r.id, "name": r.name, "host": r.host, "players": len(r.players), "max_players": r.max_players, "level_name": r.level_name, "is_random": r.is_random, "setup_character_count": getattr(r, 'setup_character_count', 0), "setup_equipment_count": getattr(r, 'setup_equipment_count', 0)} for r in visible]

manager = RoomManager()

async def verify_steam_user(ticket: str, claimed_id: str):
    return claimed_id # TRUST MODE

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    user_id = None
    
    try:
        # Handshake
        data = await websocket.receive_json()
        ticket = data.get("ticket")
        claimed_id = data.get("steam_id")
        user_id = await verify_steam_user(ticket, claimed_id)
        
        if not user_id:
            await websocket.send_text("AUTH_FAILED")
            await websocket.close()
            return

        await manager.connect(websocket, user_id)
        await websocket.send_text("SYSTEM: Welcome!") # Legacy/Simple ACK
        await asyncio.sleep(0.1)
        
        # Broadcast room list to this user
        await manager.send_json(user_id, {"type": "ROOM_LIST", "rooms": manager.get_room_list()})

        while True:
            # We expect structured JSON commands now
            raw = await websocket.receive_text()
            print(f"DEBUG RX: {raw}")
            try:
                cmd_data = json.loads(raw)
                cmd = cmd_data.get("cmd")
                
                if cmd == "GLOBAL_CHAT":
                    await manager.broadcast_lobby(cmd_data.get("txt", ""), user_id)
                    
                elif cmd == "ROOM_CHAT":
                    await manager.broadcast_room(cmd_data.get("txt", ""), user_id)
                    
                elif cmd == "CREATE_ROOM":
                    room = await manager.create_room(user_id)
                    # Notify user
                    await manager.send_json(user_id, {"type": "JOINED_ROOM", "room_id": room.id})
                    # No broadcast - clients poll every 3s
                        
                elif cmd == "JOIN_ROOM":
                    target_id = cmd_data.get("room_id")
                    room = await manager.join_room(user_id, target_id)
                    if room:
                        await manager.send_json(user_id, {"type": "JOINED_ROOM", "room_id": room.id})
                        # Send room history
                        for hist in room.chat_history:
                            await manager.send_json(user_id, {"type": "CHAT", "context": "ROOM", "txt": hist})
                    else:
                        await manager.send_json(user_id, {"type": "SYSTEM", "txt": "Room not found."})
                
                elif cmd == "LEAVE_ROOM":
                    await manager.leave_room(user_id)
                    await manager.send_json(user_id, {"type": "LEFT_ROOM"})
                    # No broadcast - clients poll every 3s

                elif cmd == "GET_ROOMS":
                    await manager.send_json(user_id, {"type": "ROOM_LIST", "rooms": manager.get_room_list()})
                    
                elif cmd == "KICK_PLAYER":
                    target_id = cmd_data.get("target_id")
                    room = manager.active_room(user_id)
                    if room and room.host == user_id:
                        if target_id in room.players and target_id != user_id:
                            await manager.leave_room(target_id)
                            await manager.send_json(target_id, {"type": "LEFT_ROOM"})
                            await manager.send_json(target_id, {"type": "SYSTEM", "txt": "You were kicked by the host."})
                    
                elif cmd == "GET_ROOM_STATE":
                    room = manager.active_room(user_id)
                    if room:
                        await manager.send_json(user_id, {
                            "type": "ROOM_STATE", 
                            "players": room.players,
                            "max_players": room.max_players,
                            "quick_match_allowed": room.quick_match_allowed,
                            "equip_timer": room.equip_timer,
                            "level_name": room.level_name,
                            "is_random": room.is_random,
                            "mercs_min": room.mercs_min,
                            "mercs_max": room.mercs_max,
                            "char_min": room.char_min,
                            "char_max": room.char_max,
                            "money_min": room.money_min,
                            "money_max": room.money_max,
                            "levels_min": room.levels_min,
                            "levels_max": room.levels_max,
                            "char_pool_size": room.char_pool_size,
                            "char_variety_min": room.char_variety_min,
                            "char_variety_max": room.char_variety_max,
                            "equip_variety_min": room.equip_variety_min,
                            "equip_variety_max": room.equip_variety_max,
                            "item_min": room.item_min,
                            "item_max": room.item_max,
                            "setup_character_count": room.setup_character_count,
                            "setup_equipment_count": room.setup_equipment_count
                        })
                
                elif cmd == "UPDATE_ROOM_SETTINGS":
                    new_limit = cmd_data.get("max_players")
                    quick_match_setting = cmd_data.get("quick_match")
                    room = manager.active_room(user_id)
                    if room and room.host == user_id:
                        changed = False
                        if new_limit and int(new_limit) >= len(room.players):
                            room.max_players = int(new_limit)
                            changed = True
                        
                        if quick_match_setting is not None:
                            print(f"[Server] Updating Quick Match Allowed: {quick_match_setting}")
                            room.quick_match_allowed = bool(quick_match_setting)
                            changed = True
                        
                        new_timer = cmd_data.get("equip_timer")
                        if new_timer is not None:
                            room.equip_timer = max(1, min(30, int(new_timer)))
                            changed = True
                            
                        new_level = cmd_data.get("level_name")
                        if new_level is not None:
                            room.level_name = str(new_level)
                            changed = True
                            
                        random_setting = cmd_data.get("is_random")
                        if random_setting is not None:
                            room.is_random = bool(random_setting)
                            changed = True
                            
                        # Batch update for all random parameters
                        param_keys = [
                            "mercs_min", "mercs_max", "char_min", "char_max",
                            "money_min", "money_max", "levels_min", "levels_max",
                            "char_pool_size", "char_variety_min", "char_variety_max",
                            "equip_variety_min", "equip_variety_max", "item_min", "item_max",
                            "setup_character_count", "setup_equipment_count"
                        ]
                        for k in param_keys:
                            val = cmd_data.get(k)
                            if val is not None:
                                setattr(room, k, int(val))
                                changed = True
                            
                        if changed:
                            await manager.broadcast_room_state(room.id)

                elif cmd == "START_GAME":
                    level_data = cmd_data.get("level_data")
                    room = manager.active_room(user_id)
                    if room and room.host == user_id and level_data:
                        # Integrity Check
                        is_valid, errors = validate_level_data(level_data)
                        if not is_valid:
                            print(f"[Server] Rejected START_GAME from {user_id}. Errors: {errors}")
                            await manager.send_json(user_id, {
                                "type": "GAME_START_REJECTED",
                                "errors": errors
                            })
                            continue

                        room.current_level_data = level_data
                        room.status = "PLAYING"
                        # Reset for new game
                        room.player_setups = {}
                        room.match_results = {}
                        room.scores = {}
                        
                        # Generate consistent level_id for sync
                        level_id = f"L_{int(asyncio.get_event_loop().time())}"
                        
                        print(f"[Server] Starting Game {level_id}. Quick Match Allowed: {room.quick_match_allowed}")
                        # Broadcast start
                        for uid in room.players:
                            await manager.send_json(uid, {
                                "type": "GAME_STARTED", 
                                "level_data": level_data,
                                "level_id": level_id,
                                "quick_match_allowed": room.quick_match_allowed,
                                "equip_timer": room.equip_timer
                            })
                        
                        # Start timer logic
                        room.time_remaining = room.equip_timer * 60
                        if room.timer_task:
                            room.timer_task.cancel()
                        room.timer_task = asyncio.create_task(manager._room_timer_logic(room.id))

                elif cmd == "SUBMIT_SETUP":
                    setup_data = cmd_data.get("setup_data")
                    room = manager.active_room(user_id)
                    if room and setup_data and room.current_level_data:
                        # Validate Setup Budget
                        is_valid, error = validate_player_setup(setup_data, room.current_level_data)
                        if not is_valid:
                            print(f"[Server] KICKING {user_id} - Setup Integrity Check Failed: {error}")
                            await manager.send_json(user_id, {"type": "SYSTEM", "txt": f"Disconnected: {error}"})
                            await manager.leave_room(user_id)
                            continue

                        room.player_setups[user_id] = setup_data
                        
                        # Check if all players ready
                        if len(room.player_setups) == len(room.players) and len(room.players) > 0:
                            # Broadcast ALL ready
                            payload = {
                                "type": "ALL_SETUPS_READY",
                                "setups": room.player_setups
                            }
                            for uid in room.players:
                                await manager.send_json(uid, payload)
                            
                            if room.timer_task:
                                room.timer_task.cancel()
                                room.timer_task = None

                elif cmd == "REPORT_RESULT":
                    opp_id = cmd_data.get("opponent_id")
                    res = cmd_data.get("result")
                    print(f"[Server] REPORT from {user_id}: vs {opp_id} = {res}")
                    
                    room = manager.active_room(user_id)
                    if room and opp_id and res:
                        if user_id not in room.match_results:
                            room.match_results[user_id] = {}
                        room.match_results[user_id][opp_id] = res
                        
                        room.match_results[user_id][opp_id] = res
                        
                        await manager.broadcast_tournament_update(room)

            except json.JSONDecodeError:
                # Fallback legacy (Global Chat)
                await manager.broadcast_lobby(raw, user_id)

    except WebSocketDisconnect:
        if user_id:
            await manager.disconnect(user_id)
            print(f"User {user_id} disconnected.")

if __name__ == "__main__":
    print("--- SERVER V2 STARTING (ROOMS ENABLED) ---")
    print("Please ensure you have stopped any previous server instances.")

    uvicorn.run(app, host="0.0.0.0", port=8000)
