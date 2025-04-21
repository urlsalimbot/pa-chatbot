import requests
import os
from flask import current_app
import openai
import mimetypes

def get_llm_response(messages, user_context=None):
    api_key = current_app.config.get('LLM_API_KEY')
    api_url = current_app.config.get('LLM_API_URL')
    model_name = current_app.config.get('LLM_MODEL_NAME', 'gemini-2.0-flash')  # Default to gemini-1.0-pro
    current_app.logger.info(f"[LLM DEBUG] api_url={api_url}, api_key={api_key}, model_name={model_name}")
    if not api_key or not api_url:
        current_app.logger.error(f"Missing LLM_API_KEY or LLM_API_URL: key={api_key}, url={api_url}")
        raise RuntimeError("LLM_API_KEY or LLM_API_URL is not set in environment/config.")

    # Ensure no double slash in endpoint
    api_url = api_url.rstrip('/')
    # Use new endpoint and model name
    endpoint = f"{api_url}/v1/models/{model_name}:generateContent?key={api_key}"
    current_app.logger.info(f"LLM endpoint: {endpoint}")

    # Google expects messages as a list of dicts with 'role' and 'parts' for Gemini
    gemini_messages = []
    for msg in messages:
        role = msg['role']
        if role == 'system':
            continue  # Gemini API does not support system messages
        # Gemini expects roles to be 'user' or 'model'
        if role == 'assistant' or role == 'bot':
            gemini_role = 'model'
        else:
            gemini_role = 'user'
        gemini_messages.append({"role": gemini_role, "parts": [{"text": msg['content']}]})

    payload = {
        "contents": gemini_messages,
        "generationConfig": {"temperature": 0.7}
    }
    # Do NOT include user_context as 'context' unless API supports it

    try:
        response = requests.post(endpoint, json=payload)
        response.raise_for_status()
        data = response.json()
        return data['candidates'][0]['content']['parts'][0]['text']
    except Exception as e:
        current_app.logger.error(f"Error calling Google LLM API: {str(e)} | Endpoint: {endpoint} | Response: {getattr(e, 'response', None)}")
        raise

# Helper: Extract text from various document types

def extract_text_from_file(file_path):
    mime_type, _ = mimetypes.guess_type(file_path)
    if mime_type == 'application/pdf':
        import pdfplumber
        with pdfplumber.open(file_path) as pdf:
            return "\n".join(page.extract_text() or '' for page in pdf.pages)
    elif mime_type == 'application/vnd.openxmlformats-officedocument.wordprocessingml.document':
        from docx import Document
        doc = Document(file_path)
        return "\n".join([p.text for p in doc.paragraphs])
    elif mime_type and mime_type.startswith('text'):
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            return f.read()
    else:
        raise ValueError('Unsupported file type for extraction')

# Document parsing via OpenAI

def parse_document_with_openai(file_path, prompt=None):
    """
    Extracts text from the document, sends it to OpenAI API for parsing/summarization, and returns the result.
    Compatible with openai>=1.0.0
    """
    openai_api_key = current_app.config.get('OPENAI_API_KEY')
    openai_model = current_app.config.get('OPENAI_MODEL', 'gpt-3.5-turbo')
    if not openai_api_key:
        raise RuntimeError('OPENAI_API_KEY not set in config.')
    openai_client = openai.OpenAI(api_key=openai_api_key)

    extracted_text = extract_text_from_file(file_path)

    # Default prompt if not provided
    if not prompt:
        prompt = (
            "Extract the key information from the following document. "
            "If the document is long, summarize the most important points. "
            "Document content:\n" + extracted_text[:6000]
        )

    response = openai_client.chat.completions.create(
        model=openai_model,
        messages=[{"role": "system", "content": "You are a document analysis assistant."},
                  {"role": "user", "content": prompt}],
        max_tokens=1024,
        temperature=0.2
    )
    return response.choices[0].message.content
