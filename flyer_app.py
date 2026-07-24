"""DealSignal — Render dashboard compatibility entry point.

Render dashboard settings specify 'gunicorn flyer_app:app'.
This file routes it to the refactored package factory.
"""

from app import create_app
app = create_app()
