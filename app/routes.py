from flask import Blueprint, request, jsonify, render_template, current_app, send_from_directory
from app.models import User, Conversation, Message
from app.utils.llm_integration import get_llm_response, parse_document_with_openai
from app.utils.database import db
from datetime import datetime
import traceback
import os
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
        data = request.json
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        user_name = data.get('user_id')  # Actually a username string from frontend
        message = data.get('message')
        conversation_id = data.get('conversation_id')
        
        if not user_name or not message:
            return jsonify({"error": "Missing required fields: user_id and message"}), 400
        
        # Get or create user by username
        user = User.query.filter_by(username=user_name).first()
        if not user:
            user = User(username=user_name)
            db.session.add(user)
            db.session.commit()
        
        # Get or create conversation
        if not conversation_id:
            conversation = Conversation(
                user_id=user.id,
                title=message
            )
            db.session.add(conversation)
            db.session.commit()
            conversation_id = conversation.id
        else:
            conversation = Conversation.query.get(conversation_id)
            if not conversation:
                return jsonify({"error": "Conversation not found"}), 404
        
        # Save user message
        user_message = Message(
            conversation_id=conversation_id,
            content=message,
            is_user=True
        )
        db.session.add(user_message)
        db.session.commit()  # Commit here to ensure message is saved even if LLM call fails
        
        # Get conversation history for context
        previous_messages = Message.query.filter_by(conversation_id=conversation_id).order_by(Message.timestamp).all()
        
        # Format messages for LLM
        messages_for_llm = [
            {"role": "system", "content": "You are a helpful personal assistant."}
        ]
        for msg in previous_messages[-10:]:
            messages_for_llm.append({
                "role": "user" if msg.is_user else "assistant",
                "content": msg.content
            })
        if not any(m.get("content") == message and m.get("role") == "user" for m in messages_for_llm):
            messages_for_llm.append({
                "role": "user",
                "content": message
            })
        try:
            llm_response = get_llm_response(messages_for_llm)
        except Exception as e:
            current_app.logger.error(f"LLM API error: {str(e)}\n{traceback.format_exc()}")
            return jsonify({"error": "Error communicating with the AI service. Please try again later."}), 500
        assistant_message = Message(
            conversation_id=conversation_id,
            content=llm_response,
            is_user=False
        )
        db.session.add(assistant_message)
        db.session.commit()
        return jsonify({
            "response": llm_response,
            "conversation_id": conversation_id,
            "user_id": user.id  # Return integer user id for frontend use if needed
        })
    except Exception as e:
        current_app.logger.error(f"Unexpected error: {str(e)}\n{traceback.format_exc()}")
        db.session.rollback()
        return jsonify({"error": "An unexpected error occurred. Please try again."}), 500

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

@bp.route('/api/conversation/<conversation_id>', methods=['GET'])
def get_conversation_messages(conversation_id):
    try:
        conversation = Conversation.query.get(conversation_id)
        if not conversation:
            return jsonify({"error": "Conversation not found"}), 404
            
        messages = Message.query.filter_by(conversation_id=conversation_id).order_by(Message.timestamp).all()
        return jsonify({
            "conversation": {
                "id": conversation.id,
                "title": conversation.title,
                "created_at": conversation.created_at.isoformat()
            },
            "messages": [{
                "id": msg.id,
                "content": msg.content,
                "is_user": msg.is_user,
                "timestamp": msg.timestamp.isoformat()
            } for msg in messages]
        })
    except Exception as e:
        current_app.logger.error(f"Error retrieving conversation messages: {str(e)}")
        return jsonify({"error": "Failed to retrieve conversation messages"}), 500

@bp.route('/api/upload', methods=['POST'])
def upload_document():
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
    if file and '.' in file.filename and file.filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS:
        filename = secure_filename(file.filename)
        file_path = os.path.join(UPLOAD_FOLDER, filename)
        file.save(file_path)
        try:
            # Parse document with OpenAI
            parsed_result = parse_document_with_openai(file_path)
            # Save as assistant message in a new conversation (or append to an existing one)
            user = User.query.first()  # Use first user as placeholder, or adjust as needed
            conversation = Conversation(user_id=user.id, title=f"Document: {filename}")
            db.session.add(conversation)
            db.session.commit()
            conversation_id = conversation.id
            assistant_message = Message(
                conversation_id=conversation_id,
                content=parsed_result,
                is_user=False
            )
            db.session.add(assistant_message)
            db.session.commit()
            return jsonify({'success': True, 'filename': filename, 'parsed_result': parsed_result, 'conversation_id': conversation_id})
        except Exception as e:
            return jsonify({'error': f'Parsing failed: {str(e)}'}), 500
    else:
        return jsonify({'error': 'File type not allowed'}), 400
