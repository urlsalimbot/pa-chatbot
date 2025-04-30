from app.models import Memory, User
from app.utils.database import db

def save_memory(user_id, key, value, category=None, day=None, schedule_id=None):
    # If saving a schedule, extract metadata
    if category == 'schedule':
        parsed_id, parsed_day, cleaned_value = parse_schedule_metadata(value)
        if parsed_id:
            schedule_id = schedule_id or parsed_id
        if parsed_day:
            day = day or parsed_day
        value = cleaned_value
    # Save by user, key, schedule_id, and day for schedules
    filters = {'user_id': user_id, 'key': key}
    if category == 'schedule':
        if schedule_id:
            filters['schedule_id'] = schedule_id
        if day:
            filters['day'] = day
    memory = Memory.query.filter_by(**filters).first()
    if memory:
        memory.value = value
        if category:
            memory.category = category
        if day:
            memory.day = day
        if schedule_id:
            memory.schedule_id = schedule_id
    else:
        memory = Memory(user_id=user_id, key=key, value=value, category=category or 'fact', day=day, schedule_id=schedule_id)
        db.session.add(memory)
    db.session.commit()
    return memory

def get_memory(user_id, key=None):
    if key:
        memory = Memory.query.filter_by(user_id=user_id, key=key).first()
        return memory.value if memory else None
    else:
        # Return all memories as a dict
        memories = Memory.query.filter_by(user_id=user_id).all()
        return {m.key: m.value for m in memories}

def detect_schedule_block(text):
    """
    Improved: Only extract a schedule block if it contains at least 2 days of the week or 2+ time slots.
    Returns the schedule as a string (multi-line if needed), or None if not found.
    """
    import re
    days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    lines = text.splitlines()
    schedule_lines = []
    found_days = set()
    found_times = 0
    for line in lines:
        l = line.lower().strip()
        for day in days:
            if day in l:
                found_days.add(day)
        if re.search(r"\b\d{1,2}:\d{2}\b", l):
            found_times += 1
        # Collect lines if they look like part of a schedule
        if any(day in l for day in days) or re.search(r"\b\d{1,2}:\d{2}\b", l) or re.match(r"\d+\. ", l):
            schedule_lines.append(line)
    # Only treat as schedule if at least 2 days or 2+ time slots are found
    if len(found_days) >= 2 or found_times >= 2:
        return '\n'.join(schedule_lines).strip()
    # Fallback: only extract after 'schedule:' if it's more than 30 chars and has at least 2 lines
    match = re.search(r'(?:my |weekly |here is my |)schedule(?: is)?[:\-]?\s*([\s\S]+)', text, re.IGNORECASE)
    if match:
        candidate = match.group(1).strip()
        if len(candidate) > 30 and candidate.count('\n') >= 1:
            return candidate
    return None

def parse_schedule_metadata(schedule_text):
    """
    Try to extract day (e.g., 'Monday', '2025-04-23') and schedule_id (e.g., 'work', 'personal') from schedule text.
    Returns a tuple (schedule_id, day, cleaned_schedule_text)
    """
    import re
    # Look for day of week and date in the format "Monday, April 30, 2025"
    day_date = None
    day = None
    
    # First try to find the full day, date format
    m = re.search(r"\b(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+([A-Z][a-z]+\s+\d{1,2},\s+\d{4})\b", schedule_text, re.IGNORECASE)
    if m:
        day_date = m.group(0)  # Full "Monday, April 30, 2025"
        day = m.group(1)       # Just "Monday"
    else:
        # If not found, look for just day of week
        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        for d in days:
            if re.search(rf"\b{d}\b", schedule_text, re.IGNORECASE):
                day = d
                break
        
        # Also try to find a date like 2025-04-23 or April 30, 2025
        date_match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", schedule_text)
        if date_match:
            if day:
                day_date = f"{day}, {date_match.group(1)}"
            else:
                day_date = date_match.group(1)
        else:
            # Try to find a date like "April 30, 2025"
            date_match = re.search(r"\b([A-Z][a-z]+\s+\d{1,2},\s+\d{4})\b", schedule_text, re.IGNORECASE)
            if date_match and day:
                day_date = f"{day}, {date_match.group(1)}"
            elif date_match:
                day_date = date_match.group(1)
    
    # Look for a schedule id (e.g., 'work', 'personal', 'school')
    schedule_id = None
    m = re.search(r"schedule for ([a-zA-Z0-9_\- ]+)", schedule_text, re.IGNORECASE)
    if m:
        schedule_id = m.group(1).strip().lower()
    
    # Clean schedule text: remove leading 'schedule for ...' or 'for ...'
    cleaned = re.sub(r"^(schedule for|for) [a-zA-Z0-9_\- ]+[:,]?", "", schedule_text, flags=re.IGNORECASE).strip()
    
    return schedule_id, day_date or day, cleaned

# Example: extract facts from message (simple rule-based, can be replaced with NLP)
def extract_fact_from_message(message):
    # Enhanced: Extract schedule, name, birthday, location, favorite color, phone
    import re
    patterns = [
        (r"my name is ([^\.\n]+)", "name"),
        (r"my birthday is ([^\.\n]+)", "birthday"),
        (r"i live in ([^\.\n]+)", "location"),
        (r"my favorite color is ([^\.\n]+)", "favorite_color"),
        (r"my phone number is ([^\.\n]+)", "phone"),
        # Schedule patterns (more flexible)
        (r"my schedule is:?\s*([\s\S]+?)(?:\n|\.|$)", "schedule"),
        (r"schedule:?\s*([\s\S]+?)(?:\n|\.|$)", "schedule"),
        (r"here is my schedule:?\s*([\s\S]+?)(?:\n|\.|$)", "schedule"),
        (r"weekly schedule:?\s*([\s\S]+?)(?:\n|\.|$)", "schedule"),
    ]
    for pattern, key in patterns:
        match = re.search(pattern, message, re.IGNORECASE)
        if match:
            return key, match.group(1).strip()
    # Try robust schedule detection
    schedule_block = detect_schedule_block(message)
    if schedule_block:
        return "schedule", schedule_block
    return None, None

def consolidate_memory_entries(user_id=None):
    """
    Checks memory entries and consolidates duplicates, prioritizing entries with more relevant information.
    
    Parameters:
    - user_id: Optional user ID to consolidate memories for a specific user. If None, consolidates for all users.
    
    Returns:
    - A tuple (consolidated_count, deleted_count) indicating how many entries were consolidated and deleted.
    """
    from app.models import Memory, User
    from app.utils.database import db
    import re
    
    consolidated_count = 0
    deleted_count = 0
    
    # Get all users if user_id is None, otherwise just the specified user
    users = [User.query.get(user_id)] if user_id else User.query.all()
    
    for user in users:
        if not user:
            continue
            
        # Get all memories for this user
        memories = Memory.query.filter_by(user_id=user.id).all()
        
        # Group memories by key
        memory_groups = {}
        for memory in memories:
            if memory.key not in memory_groups:
                memory_groups[memory.key] = []
            memory_groups[memory.key].append(memory)
        
        # Process each group of memories with the same key
        for key, group in memory_groups.items():
            if len(group) <= 1:
                continue  # No duplicates to consolidate
                
            # For schedules, handle them differently based on schedule_id and day
            if any(mem.category == 'schedule' for mem in group):
                schedule_groups = {}
                for mem in group:
                    if mem.category != 'schedule':
                        continue
                    
                    # Group by schedule_id and day
                    group_key = (mem.schedule_id or 'general', mem.day)
                    if group_key not in schedule_groups:
                        schedule_groups[group_key] = []
                    schedule_groups[group_key].append(mem)
                
                # Consolidate each schedule group
                for schedule_key, schedule_mems in schedule_groups.items():
                    if len(schedule_mems) <= 1:
                        continue
                        
                    # Find the most relevant schedule (the one with most time slots or most recent)
                    best_mem = max(schedule_mems, key=lambda m: (
                        # Count time slots (more is better)
                        len(re.findall(r'\d{1,2}:\d{2}', m.value)),
                        # If tie, use the most recently updated
                        m.updated_at
                    ))
                    
                    # Delete the others
                    for mem in schedule_mems:
                        if mem.id != best_mem.id:
                            db.session.delete(mem)
                            deleted_count += 1
                    
                    consolidated_count += 1
            else:
                # For non-schedule memories, prioritize by content length and recency
                best_mem = max(group, key=lambda m: (
                    # Longer content is likely more informative
                    len(m.value),
                    # If tie, use the most recently updated
                    m.updated_at
                ))
                
                # Delete the others
                for mem in group:
                    if mem.id != best_mem.id:
                        db.session.delete(mem)
                        deleted_count += 1
                
                consolidated_count += 1
    
    # Commit all changes
    db.session.commit()
    
    return consolidated_count, deleted_count
