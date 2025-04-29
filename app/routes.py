from flask import Blueprint, request, jsonify, render_template, current_app, send_from_directory
from app.models import User, Conversation, Message
from app.utils.llm_integration import get_llm_response, extract_text_from_file, summarize_text
from app.utils.database import db
from app.utils.memory_utils import save_memory, get_memory, extract_fact_from_message, detect_schedule_block
from datetime import datetime, timedelta
import traceback
import os
import re
from werkzeug.utils import secure_filename

UPLOAD_FOLDER = os.path.join(os.getcwd(), 'uploads')
ALLOWED_EXTENSIONS = {'pdf', 'txt', 'docx'}

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

bp = Blueprint('main', __name__)

@bp.route('/')
def index():
    return render_template('index.html')

@bp.route('/api/chat', methods=['POST'])
def chat():
    try:
        # Accept both JSON and multipart form
        if request.content_type and request.content_type.startswith('multipart/form-data'):
            user_name = request.form.get('user_id')
            message = request.form.get('message')
            conversation_id = request.form.get('conversation_id')
            file = request.files.get('file')
        else:
            data = request.json
            user_name = data.get('user_id') if data else None
            message = data.get('message') if data else None
            conversation_id = data.get('conversation_id') if data else None
            file = None
        if not user_name or not message:
            return jsonify({"error": "No user or message provided"}), 400
        # Handle file if provided
        doc_summary = None
        if file and file.filename:
            filename = secure_filename(file.filename)
            file_path = os.path.join(UPLOAD_FOLDER, filename)
            file.save(file_path)
            text = extract_text_from_file(file_path)
            doc_summary = summarize_text(text)
        # Compose message to assistant
        full_message = message
        if doc_summary:
            full_message = f"[Attached document summary: {doc_summary}]\n{message}"
        # Find or create user
        user = User.query.filter_by(username=user_name).first()
        if not user:
            user = User(username=user_name)
            db.session.add(user)
            db.session.commit()
        # Find or create conversation
        if conversation_id:
            conversation = Conversation.query.get(conversation_id)
        else:
            # Create a human-readable date for the conversation title
            now = datetime.now()
            formatted_date = now.strftime("%B %d, %Y at %I:%M %p")  # e.g. "April 23, 2025 at 12:59 PM"
            conversation = Conversation(user_id=user.id, title=f"Conversation on {formatted_date}")
            db.session.add(conversation)
            db.session.commit()
            conversation_id = conversation.id
        conversation_id = conversation.id
        # Save user message
        user_msg = Message(conversation_id=conversation_id, content=full_message, is_user=True)
        db.session.add(user_msg)
        db.session.commit()
        # --- Memory extraction and save (from both message and document) ---
        # Robustly extract and save schedule from document summary as a block, even if not labeled with 'schedule:'
        if doc_summary:
            schedule_block = None
            # Look for 'schedule:' or similar
            match = re.search(r'(?:my |weekly |here is my |)schedule(?: is)?[:\-]?\s*([\s\S]+)', doc_summary, re.IGNORECASE)
            if match and len(match.group(1).strip()) > 5:
                schedule_block = match.group(1).strip()
            # If no label, but the document is mostly a schedule (heuristic: many lines, days of week, or time patterns)
            elif len(doc_summary) > 20 and (re.search(r"monday|tuesday|wednesday|thursday|friday|saturday|sunday", doc_summary, re.IGNORECASE) or re.search(r"\d{1,2}:\d{2}", doc_summary)):
                schedule_block = doc_summary.strip()
            if schedule_block:
                save_memory(user.id, 'schedule', schedule_block, category='schedule')
                current_app.logger.info(f"Saved SCHEDULE block from document for user {user.username}")
        # Existing single-line fact extraction
        key, value = extract_fact_from_message(message)
        if key and value:
            save_memory(user.id, key, value, category='fact')
            current_app.logger.info(f"Saved memory for user {user.username}: {key} = {value}")
        if doc_summary:
            key_doc, value_doc = extract_fact_from_message(doc_summary)
            if key_doc and value_doc:
                save_memory(user.id, key_doc, value_doc, category='fact')
                current_app.logger.info(f"Saved memory from document for user {user.username}: {key_doc} = {value_doc}")
        # Chatbot response
        llm_messages = []
        user_memories = get_memory(user.id)
        memory_context = "\n".join([f"{k}: {v}" for k, v in user_memories.items()]) if user_memories else ""
        # --- New: Fetch chat history for this conversation ---
        chat_history = Message.query.filter_by(conversation_id=conversation_id).order_by(Message.timestamp.asc()).all()
        # Convert chat history to LLM message format (limit to last 20 exchanges for context)
        formatted_history = []
        for msg in chat_history[-20:]:
            role = "user" if msg.is_user else "assistant"
            formatted_history.append({"role": role, "content": msg.content})
        # Add system memory context to the front
        if memory_context:
            formatted_history.insert(0, {"role": "system", "content": f"[User facts and preferences: {memory_context}]"})
        else:
            # If no memory context, still add system time
            pass
        assistant_response = get_llm_response(formatted_history)
        # --- Extract and save facts and schedules from assistant response ---
        key_resp, value_resp = extract_fact_from_message(assistant_response)
        if key_resp and value_resp:
            # If it's a schedule, try to parse day and schedule_id
            if key_resp == 'schedule':
                # Try to extract day from the message or current date
                day = None
                schedule_id = None
                now = datetime.now()
                
                # Check for "today", "tomorrow", etc.
                if "today" in value_resp.lower():
                    day = now.strftime("%Y-%m-%d")
                    schedule_id = "today"
                elif "tomorrow" in value_resp.lower():
                    tomorrow = now + timedelta(days=1)
                    day = tomorrow.strftime("%Y-%m-%d")
                    schedule_id = "tomorrow"
                elif "yesterday" in value_resp.lower():
                    yesterday = now - timedelta(days=1)
                    day = yesterday.strftime("%Y-%m-%d")
                    schedule_id = "yesterday"
                
                save_memory(user.id, key_resp, value_resp, category='schedule', day=day, schedule_id=schedule_id)
            else:
                save_memory(user.id, key_resp, value_resp, category='fact')
            current_app.logger.info(f"Saved memory from assistant response for user {user.username}: {key_resp} = {value_resp}")
        
        # Additionally, robustly detect and save schedule blocks from Gemini response (even if not labeled)
        schedule_block = detect_schedule_block(assistant_response)
        if schedule_block and (not key_resp or key_resp != 'schedule' or schedule_block != value_resp):
            # Try to extract day and schedule_id from context
            day = None
            schedule_id = None
            now = datetime.now()
            
            # Look for day indicators in the conversation
            for msg in chat_history[-5:]:
                if msg.is_user and msg.content:
                    if "today" in msg.content.lower():
                        day = now.strftime("%Y-%m-%d")
                        schedule_id = "today"
                        break
                    elif "tomorrow" in msg.content.lower():
                        tomorrow = now + timedelta(days=1)
                        day = tomorrow.strftime("%Y-%m-%d")
                        schedule_id = "tomorrow"
                        break
                    elif "yesterday" in msg.content.lower():
                        yesterday = now - timedelta(days=1)
                        day = yesterday.strftime("%Y-%m-%d")
                        schedule_id = "yesterday"
                        break
            
            save_memory(user.id, 'schedule', schedule_block, category='schedule', day=day, schedule_id=schedule_id)
            current_app.logger.info(f"Saved SCHEDULE block from Gemini response for user {user.username}")
        assistant_msg = Message(conversation_id=conversation_id, content=assistant_response, is_user=False)
        db.session.add(assistant_msg)
        db.session.commit()
        return jsonify({"success": True, "assistant_response": assistant_response, "conversation_id": conversation_id})
    except Exception as e:
        return jsonify({"success": False, "error": str(e), "trace": traceback.format_exc()}), 500

@bp.route('/api/conversations/<user_name>', methods=['GET'])
def get_conversations(user_name):
    try:
        user = User.query.filter_by(username=user_name).first()
        if not user:
            return jsonify([])
        conversations = Conversation.query.filter_by(user_id=user.id).order_by(Conversation.created_at.desc()).all()
        return jsonify([{
            "id": conv.id,
            "title": conv.title,
            "created_at": conv.created_at.isoformat()
        } for conv in conversations])
    except Exception as e:
        current_app.logger.error(f"Error retrieving conversations: {str(e)}")
        return jsonify({"error": "Failed to retrieve conversations"}), 500

@bp.route('/api/conversations/all', methods=['GET'])
def get_all_conversations():
    try:
        conversations = Conversation.query.order_by(Conversation.created_at.desc()).all()
        return jsonify([
            {
                "id": conv.id,
                "title": conv.title,
                "created_at": conv.created_at.isoformat(),
                "user_id": conv.user_id
            } for conv in conversations
        ])
    except Exception as e:
        current_app.logger.error(f"Error retrieving all conversations: {str(e)}")
        return jsonify({"error": "Failed to retrieve conversations"}), 500

@bp.route('/api/conversation/<conversation_id>', methods=['GET'])
def get_conversation_messages(conversation_id):
    conversation = Conversation.query.get(conversation_id)
    if not conversation:
        return jsonify({"error": "Conversation not found"}), 404
    messages = Message.query.filter_by(conversation_id=conversation_id).order_by(Message.timestamp).all()
    def humanize(ts):
        # Format: 'Apr 23, 2025 10:25 AM'
        return ts.strftime('%b %d, %Y %I:%M %p') if isinstance(ts, datetime) else str(ts)
    return jsonify({
        "conversation": {
            "id": conversation.id,
            "title": conversation.title,
            "created_at": humanize(conversation.created_at)
        },
        "messages": [
            {
                "id": msg.id,
                "content": msg.content,
                "is_user": msg.is_user,
                "timestamp": humanize(msg.timestamp)
            } for msg in messages
        ]
    })

@bp.route('/api/conversation/<int:conversation_id>', methods=['DELETE'])
def delete_conversation(conversation_id):
    conversation = Conversation.query.get(conversation_id)
    if not conversation:
        return jsonify({'success': False, 'error': 'Conversation not found'}), 404
    # Delete all messages in the conversation
    Message.query.filter_by(conversation_id=conversation_id).delete()
    db.session.delete(conversation)
    db.session.commit()
    return jsonify({'success': True})

@bp.route('/api/message/<int:message_id>', methods=['DELETE'])
def delete_message(message_id):
    message = Message.query.get(message_id)
    if not message:
        return jsonify({'success': False, 'error': 'Message not found'}), 404
    db.session.delete(message)
    db.session.commit()
    return jsonify({'success': True})

@bp.route('/api/upload', methods=['POST'])
def upload_document():
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': 'No file part'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'success': False, 'error': 'No selected file'}), 400
    filename = secure_filename(file.filename)
    file_path = os.path.join(UPLOAD_FOLDER, filename)
    file.save(file_path)
    try:
        text = extract_text_from_file(file_path)
        summary = summarize_text(text)
        # Send the parsed result to the chatbot as a message from the user
        user_id = request.form.get('user_id') or request.args.get('user_id') or request.headers.get('X-User-Id')
        conversation_id = request.form.get('conversation_id') or request.args.get('conversation_id') or request.headers.get('X-Conversation-Id')
        if not user_id:
            # fallback: use first user
            user = User.query.first()
            user_id = user.id if user else None
        if user_id:
            # Find or create conversation
            if conversation_id:
                conversation = Conversation.query.get(conversation_id)
            else:
                conversation = Conversation(user_id=user_id, title=f"Document: {filename}")
                db.session.add(conversation)
                db.session.commit()
            conversation_id = conversation.id
            # Add document as user message
            user_message = Message(
                conversation_id=conversation_id,
                content=summary,
                is_user=True
            )
            db.session.add(user_message)
            db.session.commit()
            # Get Groggo's response
            llm_messages = []
            # Add memory context if available
            user_memories = get_memory(user_id)
            memory_context = "\n".join([f"{k}: {v}" for k, v in user_memories.items()]) if user_memories else ""
            if memory_context:
                llm_messages.append({"role": "user", "content": f"[User facts and preferences: {memory_context}]\n{summary}"})
            else:
                llm_messages.append({"role": "user", "content": summary})
            assistant_response = get_llm_response(llm_messages)
            assistant_message = Message(
                conversation_id=conversation_id,
                content=assistant_response,
                is_user=False
            )
            db.session.add(assistant_message)
            db.session.commit()
            return jsonify({'success': True, 'parsed_result': summary, 'assistant_response': assistant_response, 'conversation_id': conversation_id})
        else:
            return jsonify({'success': True, 'parsed_result': summary, 'assistant_response': None, 'conversation_id': None})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})
