def validate_player_setup(setup_data: list, level_data: dict) -> tuple[bool, str]:
    """
    Validates a player's submitted setup against level constraints.
    Returns (True, None) if valid, or (False, error_message) if invalid.
    """
    try:
        dialogue = level_data.get("dialogue", {})
        max_mercenaries = int(dialogue.get("max_mercenaries", 1))
        levels_available = int(dialogue.get("levels_available", 0))
        player_money = int(dialogue.get("player_money", 0))

        # Reference maps for quick lookup
        merc_pool = {m["id"]: m for m in level_data.get("mercenaries", [])}
        item_pool = {i["id"]: i for i in level_data.get("equipment", [])}
        item_costs = {i["id"]: i.get("cost", 0) for i in level_data.get("equipment", [])}

        # 1. Mercenary Count and Uniqueness Check
        if len(setup_data) > max_mercenaries:
            return False, f"Too many mercenaries: {len(setup_data)}/{max_mercenaries}"
        
        seen_merc_ids = set()
        total_levels_spent = 0
        total_equip_cost = 0

        for char_setup in setup_data:
            m_id = char_setup.get("id")
            if m_id in seen_merc_ids:
                return False, f"Duplicate mercenary ID in setup: {m_id}"
            seen_merc_ids.add(m_id)

            m_ref = merc_pool.get(m_id)
            if not m_ref:
                return False, f"Invalid mercenary ID in setup: {m_id}"

            # 2. Level Budget Check
            current_lvl = int(char_setup.get("level", 1))
            base_lvl = int(m_ref.get("level", 1))
            
            if current_lvl < base_lvl:
                return False, f"Character {m_id} level below base: {current_lvl} < {base_lvl}"
            
            total_levels_spent += (current_lvl - base_lvl)

            # 3. Equipment Integrity Check
            equip = char_setup.get("equipment", {})
            merc_slots = {s["id"]: s for s in m_ref.get("slots", [])}
            
            hand_slots_count = sum(1 for s in merc_slots.values() if s.get("type") == "hand")
            hand_item_counts = {} # item_id -> number of slots it occupies
            
            for slot_id, item_id in equip.items():
                if not item_id:
                    continue
                
                # Verify slot existence
                if slot_id not in merc_slots:
                    return False, f"Mercenary {m_id} has no slot: {slot_id}"
                
                slot_ref = merc_slots[slot_id]
                slot_type = slot_ref.get("type")
                
                # Verify item existence
                item_ref = item_pool.get(item_id)
                if not item_ref:
                    return False, f"Invalid item ID {item_id} in slot {slot_id}"
                
                item_type = item_ref.get("slot_type")
                
                # Compatibility Check
                if item_type == "two_handed":
                    if slot_type != "hand":
                        return False, f"Two-handed item {item_id} placed in non-hand slot {slot_id}"
                elif item_type != slot_type:
                    return False, f"Item {item_id} (type {item_type}) incompatible with slot {slot_id} (type {slot_type})"
                
                # Track hand usage
                if slot_type == "hand":
                    hand_item_counts[item_id] = hand_item_counts.get(item_id, 0) + 1
                
                # We skip cost calculation here and do it per unique item assignment later 
                # to handle 2H items correctly (one item, multiple slots).
                # Actually, 1H items in different slots SHOULD be charged separately.
                # So we need to distinguish between "one 2H item in two slots" 
                # and "two identical 1H items in two slots".

            # Hand Capacity Check
            total_hand_usage = 0
            char_equip_cost = 0
            
            # Process non-hand items cost
            for slot_id, item_id in equip.items():
                if not item_id: continue
                if merc_slots[slot_id].get("type") != "hand":
                    char_equip_cost += item_costs.get(item_id, 0)

            # Process hand items capacity and cost
            for item_id, count in hand_item_counts.items():
                item_ref = item_pool[item_id]
                item_cost = item_costs.get(item_id, 0)
                
                if item_ref.get("slot_type") == "two_handed":
                    # Two-handed weapon: count as 2 capacity, but charged once per pair (or partial pair)
                    num_instances = (count + 1) // 2
                    total_hand_usage += num_instances * 2
                    char_equip_cost += num_instances * item_cost
                else:
                    # One-handed weapon: count as 1 capacity, charged once per slot
                    total_hand_usage += count
                    char_equip_cost += count * item_cost
            
            if total_hand_usage > hand_slots_count:
                return False, f"Mercenary {m_id} hand capacity exceeded: {total_hand_usage}/{hand_slots_count}"
            
            total_equip_cost += char_equip_cost

        # Final Budget Validations
        if total_levels_spent > levels_available:
            return False, f"Level points budget exceeded: {total_levels_spent}/{levels_available}"
        
        if total_equip_cost > player_money:
            return False, f"Money budget exceeded: {total_equip_cost}/{player_money}"

        return True, None

    except Exception as e:
        import traceback
        traceback.print_exc()
        return False, f"Setup validation error: {str(e)}"
