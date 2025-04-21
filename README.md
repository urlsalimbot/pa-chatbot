# Personal Assistant Chatbot

A web-based personal assistant chatbot built with Flask, PostgreSQL, and a third-party LLM API.

## Features

- Real-time chat interface
- Conversation history and management
- PostgreSQL database for persistent storage
- Integration with third-party LLM API for intelligent responses

## Setup Instructions

### Prerequisites

- Python 3.8+
- PostgreSQL
- API key for a third-party LLM service

### Installation

1. Clone the repository
2. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
3. Set up your PostgreSQL database:
   ```
   psql -U postgres -f initdb.sql
   ```
4. Configure environment variables in `.env` file:
   ```
   DB_NAME=assistant_db
   DB_USER=assistant_user
   DB_PASSWORD=yourpassword
   DB_HOST=localhost
   LLM_API_KEY=your_llm_api_key
   LLM_API_URL=https://api.llm-provider.com/v1/chat
   SECRET_KEY=your_secret_key
   ```
5. Initialize the database with Flask-Migrate:
   ```
   flask db upgrade
   ```
6. Run the application:
   ```
   python run.py
   ```
7. Access the application at http://localhost:5000

## Project Structure

- `app/` - Main application package
  - `__init__.py` - Application factory
  - `models.py` - Database models
  - `routes.py` - API routes
  - `templates/` - HTML templates
  - `utils/` - Utility modules
- `migrations/` - Database migration files
- `config.py` - Configuration settings
- `run.py` - Application entry point

## API Endpoints

- `GET /` - Main chat interface
- `POST /api/chat` - Send a message and get a response
- `GET /api/conversations/<user_id>` - Get all conversations for a user
- `GET /api/conversation/<conversation_id>` - Get messages for a specific conversation
