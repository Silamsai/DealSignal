#!/usr/bin/env python3
"""DealSignal — application entry point.

Usage:
    Development:  python run.py
    Production:   gunicorn run:app --bind 0.0.0.0:$PORT --workers 1
"""

from app import create_app

app = create_app()

if __name__ == "__main__":
    print("DealSignal")
    print("  Home (choose path): http://localhost:5000/")
    print("  Investor page:      http://localhost:5000/landing")
    print("  Seller page:        http://localhost:5000/sell")
    print("  Staff login:        http://localhost:5000/login → /app")
    print("  Health check:       http://localhost:5000/healthz")
    app.run(debug=True, port=5000)
