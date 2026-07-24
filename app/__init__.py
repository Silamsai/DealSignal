"""Flask application factory."""

import logging
import os
import sys

from flask import Flask

from app.config import PERMANENT_SESSION_LIFETIME, SECRET_KEY


def create_app() -> Flask:
    """Create and configure the Flask application."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    logger = logging.getLogger(__name__)

    app = Flask(
        __name__,
        template_folder=os.path.join(os.path.dirname(__file__), "templates"),
        static_folder=os.path.join(os.path.dirname(__file__), "static"),
    )

    app.secret_key = SECRET_KEY
    app.permanent_session_lifetime = PERMANENT_SESSION_LIFETIME

    # Initialise admin password hash from env
    from app.auth import init_admin_hash
    init_admin_hash()

    # Register blueprint with all routes
    from app.routes import bp
    app.register_blueprint(bp)

    logger.info(
        "DealSignal app created (test_mode=%s)",
        os.environ.get("STANNP_TEST_MODE", "1"),
    )
    return app
