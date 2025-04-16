from flask import Flask
from config import Config
from app.utils.database import db
from flask_migrate import Migrate

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    
    db.init_app(app)
    migrate = Migrate(app, db)  # Add this line
    
    # Remove the db.create_all() call - migrations will handle this
    
    from app import routes
    app.register_blueprint(routes.bp)
    
    return app