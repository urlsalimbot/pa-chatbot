import requests
from config import Config

def get_llm_response(messages, user_context=None):
    headers = {
        "Authorization": f"Bearer {Config.LLM_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": "gpt-4",  # or whichever model you're using
        "messages": messages,
        "temperature": 0.7
    }
    
    if user_context:
        payload["context"] = user_context
    
    response = requests.post(Config.LLM_API_URL, json=payload, headers=headers)
    response.raise_for_status()
    
    return response.json()["choices"][0]["message"]["content"]
