import requests
import os
from flask import current_app
import mimetypes
from datetime import datetime, timedelta
import re
from app.utils.memory_utils import get_memory, consolidate_memory_entries
from app.models import Memory, Conversation, Message, User

def parse_and_sort_schedule_entries(schedule_text):
    """
    Parse a schedule text and sort entries chronologically by time.
    Returns a sorted list of (time_slot, event) tuples.
    """
    entries = []
    lines = schedule_text.strip().split('\n')
    
    for line in lines:
        # Skip empty lines or lines without time information
        if not line.strip() or ':' not in line:
            continue
            
        # Try to extract time and event from lines like "- 9:00 AM: Meeting" or "9:00 AM: Meeting"
        # Also handle formats like "- From 9:00 AM to 10:00 AM: Meeting"
        time_match = re.search(r'(?:^|\s)(\d{1,2}(?::\d{2})?\s*(?:AM|PM|am|pm)?(?:\s*-\s*\d{1,2}(?::\d{2})?\s*(?:AM|PM|am|pm)?)?)', line)
        
        if time_match:
            time_str = time_match.group(1).strip()
            # Find the position after the time string
            pos = line.find(time_str) + len(time_str)
            
            # Check if there's a colon after the time
            remaining = line[pos:].strip()
            if remaining.startswith(':'):
                event = remaining[1:].strip()
            else:
                # If no colon, just take the rest of the line
                event = remaining
                
            # For sorting, convert time to 24-hour format
            sort_time = time_str.lower()
            if 'am' in sort_time or 'pm' in sort_time:
                # Handle AM/PM format
                hour = int(re.search(r'(\d{1,2})', sort_time).group(1))
                if 'pm' in sort_time and hour < 12:
                    hour += 12
                if 'am' in sort_time and hour == 12:
                    hour = 0
                    
                # Extract minutes if present
                minutes = 0
                min_match = re.search(r':(\d{2})', sort_time)
                if min_match:
                    minutes = int(min_match.group(1))
                    
                # Use hour and minutes for sorting
                sort_key = hour * 60 + minutes
            else:
                # Handle 24-hour format or just hour
                hour = int(re.search(r'(\d{1,2})', sort_time).group(1))
                minutes = 0
                min_match = re.search(r':(\d{2})', sort_time)
                if min_match:
                    minutes = int(min_match.group(1))
                sort_key = hour * 60 + minutes
                
            entries.append((sort_key, time_str, event))
        else:
            # Handle lines without clear time format but with event information
            # These will be placed at the end
            entries.append((float('inf'), "", line.strip()))
    
    # Sort entries by the numeric sort_key
    entries.sort(key=lambda x: x[0])
    
    # Return only the time string and event
    return [(entry[1], entry[2]) for entry in entries]

def format_schedule_chronologically(schedule_text):
    """
    Takes a schedule text and returns a chronologically sorted version.
    """
    # Parse the header (if any)
    header = ""
    content = schedule_text
    
    # Check if there's a header line before the schedule items
    lines = schedule_text.strip().split('\n')
    if lines and not re.search(r'(?:^|\s)(\d{1,2}(?::\d{2})?\s*(?:AM|PM|am|pm)?)', lines[0]):
        header = lines[0]
        content = '\n'.join(lines[1:])
    
    # Parse and sort the schedule entries
    sorted_entries = parse_and_sort_schedule_entries(content)
    
    # Rebuild the schedule text with sorted entries
    result = header + "\n" if header else ""
    for time_str, event in sorted_entries:
        if time_str:
            result += f"- {time_str}: {event}\n"
        else:
            result += f"- {event}\n"
            
    return result.strip()

def get_llm_response(messages, user_context=None):
    from app.utils.memory_utils import get_memory, consolidate_memory_entries
    api_key = current_app.config.get('LLM_API_KEY')
    api_url = current_app.config.get('LLM_API_URL')
    model_name = current_app.config.get('LLM_MODEL_NAME', 'gemini-2.0-flash')
    now = datetime.now()
    today_string = now.strftime('%A, %B %d, %Y')
    
    # Periodically consolidate memory entries (every 10th request, roughly)
    import random
    if random.random() < 0.1:  # 10% chance to run consolidation
        try:
            # Get user_id if available
            user_id = None
            for msg in reversed(messages):
                if msg.get('role') == 'user' and 'user_id' in msg:
                    user_id = msg['user_id']
                    break
            if user_context and 'user_id' in user_context:
                user_id = user_context['user_id']
                
            # Run consolidation for this user only
            if user_id:
                consolidated, deleted = consolidate_memory_entries(user_id)
                if consolidated > 0 or deleted > 0:
                    current_app.logger.info(f"Memory consolidation: {consolidated} groups consolidated, {deleted} entries deleted for user {user_id}")
        except Exception as e:
            current_app.logger.error(f"Error during memory consolidation: {str(e)}")
    
    # --- NEW: Fetch all conversations for global context ---
    try:
        all_convs = Conversation.query.order_by(Conversation.created_at.desc()).limit(10).all()  # Limit to last 10 for brevity
        all_context = []
        for conv in all_convs:
            user = User.query.get(conv.user_id)
            user_name = user.username if user else f"User {conv.user_id}"
            # Get first and last message for summary
            msgs = Message.query.filter_by(conversation_id=conv.id).order_by(Message.timestamp).all()
            if msgs:
                first_msg = msgs[0].content[:100]
                last_msg = msgs[-1].content[:100] if len(msgs) > 1 else ''
                all_context.append(f"Conversation {conv.id} (User: {user_name}, Title: '{conv.title}', Created: {conv.created_at.date()}):\n- First: {first_msg}\n- Last: {last_msg}")
            else:
                all_context.append(f"Conversation {conv.id} (User: {user_name}, Title: '{conv.title}', Created: {conv.created_at.date()}): No messages.")
        all_conv_summary = '\n\n'.join(all_context)
    except Exception as e:
        all_conv_summary = f"[Error retrieving all conversations: {str(e)}]"

    # --- Insert global conversation summary as system message ---
    system_all_conv_message = {"role": "system", "content": f"Summary of recent conversations across all users:\n{all_conv_summary}"}

    # Existing system time message
    system_time_message = {"role": "system", "content": f"Today is {today_string}. Always be aware of the current day when answering questions about schedules, dates, or events."}

    user_id = None
    for msg in reversed(messages):
        if msg.get('role') == 'user' and 'user_id' in msg:
            user_id = msg['user_id']
            break
    if user_context and 'user_id' in user_context:
        user_id = user_context['user_id']
    memory_message = None
    if user_id:
        all_memories = Memory.query.filter_by(user_id=user_id).all()
        facts = []
        schedules = {}
        for mem in all_memories:
            if mem.category == 'fact':
                facts.append(f"{mem.key}: {mem.value}")
            elif mem.category == 'schedule':
                schedule_key = f"{mem.schedule_id or 'general'}"
                if mem.day:
                    schedule_key += f" ({mem.day})"
                if schedule_key not in schedules:
                    schedules[schedule_key] = mem.value
        memory_context = []
        if facts:
            memory_context.append("User Facts:")
            memory_context.extend([f"- {fact}" for fact in facts])
        if schedules:
            memory_context.append("\nUser Schedules:")
            for key, value in schedules.items():
                memory_context.append(f"- Schedule for {key}:")
                # Sort schedule entries chronologically
                sorted_schedule = format_schedule_chronologically(value)
                memory_context.append(f"  {sorted_schedule}")
        if memory_context:
            memory_message = {"role": "system", "content": "\n".join(memory_context)}
    llm_messages = [system_time_message]
    if user_context:
        llm_messages.append({"role": "system", "content": f"[User facts and preferences: {user_context}]"})
    if memory_message:
        llm_messages.append(memory_message)

    # --- Check memory and conversations for schedule match before prompting chatbot ---
    # If user is asking about a schedule, try to extract the requested day/context
    import re
    requested_schedule = None
    for msg in reversed(messages):
        if msg.get('role') == 'user':
            # Enhanced pattern to detect more schedule-related queries
            match = re.search(r'(schedule|plans?|appointments?|events?|what\'s|what is|do i have|anything) (for|on|tomorrow|today|yesterday|this|next|monday|tuesday|wednesday|thursday|friday|saturday|sunday) ([^\?\.!]*)', msg.get('content', ''), re.IGNORECASE)
            if match:
                day_term = match.group(2).lower()
                additional = match.group(3).strip()
                
                # Handle time references
                now = datetime.now()
                if day_term == 'tomorrow':
                    requested_schedule = 'tomorrow'
                    requested_date = (now + timedelta(days=1)).strftime("%Y-%m-%d")
                elif day_term == 'today':
                    requested_schedule = 'today'
                    requested_date = now.strftime("%Y-%m-%d")
                elif day_term == 'yesterday':
                    requested_schedule = 'yesterday'
                    requested_date = (now - timedelta(days=1)).strftime("%Y-%m-%d")
                elif day_term in ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']:
                    requested_schedule = day_term
                else:
                    requested_schedule = (day_term + ' ' + additional).strip()
                break
    
    # Directly query the Memory table for schedules matching the requested day
    matching_schedules = []
    if requested_schedule and user_id:
        # Try to find schedules in memory with matching day or schedule_id
        if requested_schedule in ['today', 'tomorrow', 'yesterday']:
            # For these specific terms, we have exact dates
            now = datetime.now()
            if requested_schedule == 'tomorrow':
                target_date = (now + timedelta(days=1)).strftime("%Y-%m-%d")
            elif requested_schedule == 'today':
                target_date = now.strftime("%Y-%m-%d")
            elif requested_schedule == 'yesterday':
                target_date = (now - timedelta(days=1)).strftime("%Y-%m-%d")
                
            # Find memories with matching day or schedule_id
            schedule_memories = Memory.query.filter(
                Memory.user_id == user_id,
                Memory.category == 'schedule',
                (Memory.day == target_date) | (Memory.schedule_id == requested_schedule)
            ).all()
            
            if schedule_memories:
                for mem in schedule_memories:
                    # Sort schedule entries chronologically
                    sorted_schedule = format_schedule_chronologically(mem.value)
                    matching_schedules.append(f"Schedule for {mem.schedule_id or 'unknown'} on {mem.day or 'unknown date'}:\n{sorted_schedule}")
    
    schedule_convs = []
    if requested_schedule and user_id:
        # Find conversations for this user tagged with 'schedule' and matching the requested context
        tag_filter = '%schedule%'
        convs = Conversation.query.filter(Conversation.user_id==user_id, Conversation.tags.ilike(tag_filter)).all()
        for conv in convs:
            # Optionally, check if requested_schedule is in title or lead
            if (requested_schedule.lower() in (conv.title or '').lower()) or (requested_schedule.lower() in (conv.lead or '').lower()):
                schedule_convs.append(conv)
    
    # Summarize schedule conversations
    if matching_schedules or schedule_convs:
        schedule_context = []
        # First add direct memory matches
        if matching_schedules:
            schedule_context.append("Matching schedules from memory:")
            for schedule in matching_schedules:
                schedule_context.append(schedule)
        # Then add conversation matches
        for conv in schedule_convs:
            msgs = Message.query.filter_by(conversation_id=conv.id).order_by(Message.timestamp).all()
            summary = f"Conversation {conv.id} (Title: '{conv.title}', Lead: '{conv.lead}'):\n"
            for msg in msgs:
                summary += f"- {'User' if msg.is_user else 'Assistant'}: {msg.content[:100]}\n"
            schedule_context.append(summary)
        llm_messages.append({"role": "system", "content": "Relevant schedule conversations:\n" + '\n'.join(schedule_context)})

    identity_message = {"role": "system", "content": (
        "You are Groggo - The Personal Assistant, a friendly dinosaur assistant who helps users manage their schedules, remember facts, and answer questions. "
        "When presenting a schedule, ALWAYS format it as follows (replace with actual details):\n"
        "Schedule for [Day], [Date]:\n"
        "- [Time Slot 1]: [Event or Task 1]\n"
        "- [Time Slot 2]: [Event or Task 2]\n\n"
        "IMPORTANT INSTRUCTION: When a user provides you with a schedule, you MUST repeat it back to them in a well-formatted way. "
        "Always confirm the schedule by saying something like 'I've saved your schedule for [Day], [Date]' and then repeat the schedule in the format above. "
        "This is critical as your response will be used to extract and save the schedule information. "
        "ALWAYS include both the day of the week AND the date in the format 'Monday, April 30, 2025' in your schedule headers."
    )}
    llm_messages.append(identity_message)
    llm_messages.extend(messages)
    if not api_key or not api_url:
        user_message = messages[-1]["content"] if messages else ""
        response = (
            f"Rawr! I'm Groggo the dinosaur assistant. Today is {today_string}. "
            f"{'User\'s schedule: ' + schedules.get('general', '') if user_id and memory_message and 'schedules' in locals() else ''}"
            "I see you said: '" + user_message[:120] + "'\n"
            "(Gemini API is unavailable, so this is a local fallback response.)"
        )
        return response
    api_url = api_url.rstrip('/')
    endpoint = f"{api_url}/v1/models/{model_name}:generateContent?key={api_key}"
    gemini_messages = []
    inserted_identity = False
    for idx, msg in enumerate(llm_messages):
        role = msg['role']
        if not inserted_identity and idx == 0:
            gemini_messages.append({"role": "user", "parts": [{"text": identity_message["content"]}]})
            inserted_identity = True
        if role == 'system':
            gemini_messages.append({"role": "user", "parts": [{"text": msg['content']}]})
            continue
        gemini_role = 'model' if role in ('assistant', 'bot') else 'user'
        gemini_messages.append({"role": gemini_role, "parts": [{"text": msg['content']}]})
    payload = {
        "contents": gemini_messages,
        "generationConfig": {"temperature": 0.7}
    }
    try:
        response = requests.post(endpoint, json=payload, timeout=15)
        response.raise_for_status()
        data = response.json()
        return data['candidates'][0]['content']['parts'][0]['text']
    except Exception as e:
        user_message = messages[-1]["content"] if messages else ""
        return (
            f"Rawr! I'm Groggo the dinosaur assistant. Today is {today_string}. "
            f"{'User\'s schedule: ' + schedules.get('general', '') if user_id and memory_message and 'schedules' in locals() else ''}"
            "I see you said: '" + user_message[:120] + "'\n"
            "(Gemini API is unavailable, so this is a local fallback response.)"
        )

# Helper: Extract text from various document types (local only)
def extract_text_from_file(file_path):
    import mimetypes
    mime_type, _ = mimetypes.guess_type(file_path)
    if mime_type == 'application/pdf':
        import pdfplumber
        with pdfplumber.open(file_path) as pdf:
            return "\n".join(page.extract_text() or '' for page in pdf.pages)
    elif mime_type == 'application/vnd.openxmlformats-officedocument.wordprocessingml.document':
        from docx import Document
        doc = Document(file_path)
        return "\n".join([p.text for p in doc.paragraphs])
    elif mime_type == 'text/plain':
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()
    else:
        return "Unsupported file type. Only PDF, DOCX, and TXT are supported."

# Local document summary (simple)
def summarize_text(text, max_length=400):
    # Simple summary: return the first max_length characters
    if len(text) > max_length:
        return text[:max_length] + '...'
    return text
