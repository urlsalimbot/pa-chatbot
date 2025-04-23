import requests
import os
from flask import current_app
import mimetypes
from datetime import datetime
from app.utils.memory_utils import get_memory
from app.models import Memory

def get_llm_response(messages, user_context=None):
    from app.utils.memory_utils import get_memory
    api_key = current_app.config.get('LLM_API_KEY')
    api_url = current_app.config.get('LLM_API_URL')
    model_name = current_app.config.get('LLM_MODEL_NAME', 'gemini-2.0-flash')
    now = datetime.now()
    today_string = now.strftime('%A, %B %d, %Y')
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
                memory_context.append(f"  {value}")
        if memory_context:
            memory_message = {"role": "system", "content": "\n".join(memory_context)}
    sys_msgs = [system_time_message]
    if memory_message:
        sys_msgs.append(memory_message)
    identity_message = {"role": "system", "content": (
        "You are Groggo - The Personal Assistant, a friendly dinosaur assistant who helps users manage their schedules, remember facts, and answer questions. "
        "When presenting a schedule, ALWAYS format it as follows (replace with actual details):\n"
        "Schedule for [Day or Date or Context]:\n"
        "- [Time Slot 1]: [Event or Task 1]\n"
        "- [Time Slot 2]: [Event or Task 2]\n"
        "If there is no schedule for a given day, say: 'There is no schedule for [Day]'.\n"
        "Always use this format when asked about a schedule."
    )}
    sys_msgs.append(identity_message)
    for sys_msg in reversed(sys_msgs):
        if not messages or messages[0].get("content", "").find(sys_msg["content"]) == -1:
            messages = [sys_msg] + messages
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
    for idx, msg in enumerate(messages):
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
