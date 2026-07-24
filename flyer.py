#!/usr/bin/env python3
"""DealSignal — backward-compatible entry point.

The application has been restructured into the app/ package.
Use `python run.py` as the canonical entry point.
This file is kept so that `python flyer.py` still works.
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
