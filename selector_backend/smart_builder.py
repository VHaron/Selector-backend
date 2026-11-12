from datetime import datetime, timedelta

def parse_time(t_str):
    if not t_str: return datetime.now()
    try:
        return datetime.strptime(t_str.replace('T', ' ')[:16] + ':00', "%Y-%m-%d %H:%M:%S")
    except:
        return datetime.now()

def build_smart_grid(tournaments, budget: float, max_tables: int, start_time_str: str, end_time_str: str):
    start_time = parse_time(start_time_str)
    end_time = parse_time(end_time_str)

    valid_tournaments = []
    for t in tournaments:
        try:
            buyin_str = str(t.get('buyin', '0')).replace('$', '').replace('€', '').replace(',', '.')
            buyin = float(buyin_str) if buyin_str else 0.0
            
            ev_str = str(t.get('ev_mtt', '0')).replace(',', '.')
            ev = float(ev_str) if ev_str else 0.0
            
            dur_str = str(t.get('duration', '0')).replace(',', '.')
            duration = float(dur_str) * 60 if dur_str else 120
            
            t_start = parse_time(t.get('time_start', ''))
            t_end = t_start + timedelta(minutes=duration)
            
            if t_start >= start_time and t_end <= end_time and buyin <= budget and buyin > 0:
                valid_tournaments.append({
                    'data': t,
                    'buyin': buyin,
                    'ev': ev,
                    'start_min': int(t_start.timestamp() // 60),
                    'end_min': int(t_end.timestamp() // 60)
                })
        except Exception:
            continue

    valid_tournaments.sort(key=lambda x: x['ev'], reverse=True)

    selected_grid = []
    current_spent = 0
    timeline = {}

    for t in valid_tournaments:
        if current_spent + t['buyin'] > budget:
            continue

        can_add = True
        for minute in range(t['start_min'], t['end_min']):
            if timeline.get(minute, 0) >= max_tables:
                can_add = False
                break
        
        if can_add:
            selected_grid.append(t['data'])
            current_spent += t['buyin']
            for minute in range(t['start_min'], t['end_min']):
                timeline[minute] = timeline.get(minute, 0) + 1

    return selected_grid
