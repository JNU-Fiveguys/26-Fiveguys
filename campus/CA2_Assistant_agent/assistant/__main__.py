"""python -m assistant — 프로토타입 로컬 서버 (http://127.0.0.1:8765). 포트는 CA2_PORT."""
import uvicorn

from . import config as C

if __name__ == "__main__":
    print(f"CA2 프로토타입: http://{C.HOST}:{C.PORT}")
    uvicorn.run("assistant.api:app", host=C.HOST, port=C.PORT, log_level="warning", access_log=False)
