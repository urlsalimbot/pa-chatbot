import os
from dotenv import load_dotenv

load_dotenv()

class Config():
    SECRET_KEY  = os.getenv('SECRET_KEY', 'default-secret-key')
    SQLALCHEMY_DATABASE_URI = f"postgresql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}@{os.getenv('DB_HOST')}/{os.getenv('DB_NAME')}"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    LLM_API_KEY = os.getenv('LLM_API_KEY')
    LLM_API_URL = os.getenv('LLM_API_URL')
