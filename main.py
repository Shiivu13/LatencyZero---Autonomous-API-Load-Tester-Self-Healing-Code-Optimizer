import os

import uvicorn

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    print(f"LatencyZero dashboard: http://localhost:{port}/")
    uvicorn.run("target_app.app:app", host="0.0.0.0", port=port)
