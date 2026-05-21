from flask import Flask
from flask_cors import CORS
from config import DB_PATH
from models import Base, engine

app = Flask(__name__)
CORS(app)

Base.metadata.create_all(bind=engine)

from routes.scan import scan_bp
from routes.images import images_bp
from routes.export import export_bp
from routes.pending import pending_bp

app.register_blueprint(scan_bp, url_prefix='/api')
app.register_blueprint(images_bp, url_prefix='/api')
app.register_blueprint(export_bp, url_prefix='/api')
app.register_blueprint(pending_bp, url_prefix='/api')

from routes.export import cleanup_old_exports
cleanup_old_exports()

if __name__ == '__main__':
    app.run(debug=True, port=5000)
