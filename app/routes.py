from flask import Blueprint, request, jsonify, render_template
from app.models import User, Conversation, Message
from app.utils.llm_integration import get_llm_response
from app.utils.database import db
from datetime import datetime

bp = Blueprint('main', __name__)

@bp.route('/')
def index():
    return render_template('index.html')

@bp.route('/api/chat', methods=['POST'])
def chat():
    data = request.json
    user_id = data.get('user_id')
    message = data.get('message')
    conversation_id = data.get('conversation_id')
    
    # Get or create user
    user = User.query.get(user_id)
    if not user:
        user = User(username=f"user_{user_id}")
        db.session.add(user)
        db.session.commit()
    
    # Get or create conversation
    if not conversation_id:
        conversation = Conversation(
            user_id=user.id,
            title=f"Conversation {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )
        db.session.add(conversation)
        db.session.commit()
        conversation_id = conversation.id
    else:
        conversation = Conversation.query.get(conversation_id)
    
    # Save user message
    user_message = Message(
        conversation_id=conversation_id,
        content=message,
        is_user=True
    )
    db.session.add(user_message)
    
    # Get conversation history for context
    previous_messages = Message.query.filter_by(conversation_id=conversation_id).order_by(Message.timestamp).all()
    
    # Format messages for LLM
    messages_for_llm = [
        {"role": "system", "content": "You are a helpful personal assistant."}
    ]
    for msg in previous_messages:
        messages_for_llm.append({
            "role": "user" if msg.is_user else "assistant",
            "content": msg.content
        })
    
    # Get LLM response
    try:
        llm_response = get_llm_response(messages_for_llm)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    
    # Save assistant response
    assistant_message = Message(
        conversation_id=conversation_id,
        content=llm_response,
        is_user=False
    )
    db.session.add(assistant_message)
    db.session.commit()
    
    return jsonify({
        "response": llm_response,
        "conversation_id": conversation_id
    })

@bp.route('/api/conversations/<user_id>', methods=['GET'])
def get_conversations(user_id):
    user = User.query.get(user_id)
    if not user:
        return jsonify([])
    
    conversations = Conversation.query.filter_by(user_id=user.id).order_by(Conversation.created_at.desc()).all()
    return jsonify([{
        "id": conv.id,
        "title": conv.title,
        "created_at": conv.created_at.isoformat()
    } for conv in conversations])
