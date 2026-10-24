import os

from billing import create_app

app = create_app()

if __name__ == "__main__":
    from waitress import serve

    port = int(os.environ.get("PORT", 5000))
    print(f"Billing app running: open http://localhost:{port} (other devices: http://<this-pc-ip>:{port})")
    serve(app, host="0.0.0.0", port=port, threads=8)
