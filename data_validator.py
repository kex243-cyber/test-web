def validate_level_data(level_data: dict) -> tuple[bool, list[str]]:
    """
    Checks integrity of level data based on summarized game limits.
    Returns (is_valid, error_list)
    """
    errors = []
    
    # 1. Dialogue Section
    if "dialogue" in level_data:
        d = level_data["dialogue"]
        if d.get("player_money", 0) > 9999 or d.get("player_money", 0) < -9999:
            errors.append(f"Invalid Money: {d.get('player_money')}")
        if d.get("max_mercenaries", 0) > 10 or d.get("max_mercenaries", 0) < 1:
            errors.append(f"Invalid Max Mercs: {d.get('max_mercenaries')}")
        if d.get("levels_available", 0) > 90 or d.get("levels_available", 0) < 0:
            errors.append(f"Invalid Level Points: {d.get('levels_available')}")
    else:
        errors.append("Missing dialogue section")

    # 2. Mercenaries List (Pool for players)
    mercs = level_data.get("mercenaries", [])
    if len(mercs) > 16:
         errors.append(f"Too many mercenaries in pool: {len(mercs)}/16")
    for i, m in enumerate(mercs):
        m_errs = _validate_character(m, f"Mercenary[{i}]")
        errors.extend(m_errs)

    # 3. Enemies List (Pre-placed)
    enemies = level_data.get("enemies", [])
    if len(enemies) > 10:
         errors.append(f"Too many pre-placed enemies: {len(enemies)}/10")
    for i, m in enumerate(enemies):
        m_errs = _validate_character(m, f"Enemy[{i}]")
        errors.extend(m_errs)

    # 4. Equipment (Shop Pack)
    shop = level_data.get("equipment", [])
    if len(shop) > 100:
        errors.append(f"Shop Pack exceeds 100 items: {len(shop)}")
    for i, item in enumerate(shop):
        i_errs = _validate_item(item, f"ShopItem[{i}]")
        errors.extend(i_errs)

    # 5. Enemy Equipment (Enemy Pack)
    e_equip = level_data.get("enemy_equipment", [])
    if len(e_equip) > 100:
        errors.append(f"Enemy Pack exceeds 100 items: {len(e_equip)}")
    for i, item in enumerate(e_equip):
        i_errs = _validate_item(item, f"EnemyItem[{i}]")
        errors.extend(i_errs)

    return len(errors) == 0, errors

def _validate_character(m, label):
    errs = []
    # Stats range check
    for s in ["health", "attack", "defence", "speed", "cost"]:
        val = m.get(s, 0)
        if val > 9999 or val < -9999:
            errs.append(f"{label} {s} out of bounds: {val}")
    
    # Growth checks
    growth = m.get("level_growth", {})
    for s, val in growth.items():
        if val > 9999 or val < -9999:
            errs.append(f"{label} growth_{s} out of bounds: {val}")
            
    # Level checks
    lvl = m.get("level", 1)
    max_lvl = m.get("max_level", 1)
    if lvl < 1 or lvl > 10: errs.append(f"{label} level invalid: {lvl}")
    if max_lvl < 1 or max_lvl > 10: errs.append(f"{label} max_level invalid: {max_lvl}")
    
    # List counts
    slots = m.get("slots", [])
    if len(slots) > 20: errs.append(f"{label} too many slots: {len(slots)}")
    
    # Level abilities (dict of dicts)
    ab_count = 0
    level_abilities = m.get("level_abilities", {})
    if isinstance(level_abilities, dict):
        for lvl_str, abs_dict in level_abilities.items():
            if isinstance(abs_dict, dict):
                ab_count += len(abs_dict)
    elif isinstance(level_abilities, list):
        ab_count = len(level_abilities)
        
    if ab_count > 20: errs.append(f"{label} too many total level abilities: {ab_count}")
    
    return errs

def _validate_item(item, label):
    errs = []
    # Stats & Cost
    cost = item.get("cost", 0)
    if cost > 9999 or cost < -9999: errs.append(f"{label} cost OOB: {cost}")
    
    stats = item.get("stats", {})
    for s, val in stats.items():
        if val > 9999 or val < -9999:
            errs.append(f"{label} stat {s} OOB: {val}")
            
    # Abilities (dict)
    abs_dict = item.get("abilities", {})
    if len(abs_dict) > 10:
        errs.append(f"{label} too many abilities: {len(abs_dict)}/10")
        
    return errs
