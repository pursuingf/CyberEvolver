from fastapi import FastAPI

from server.api.kb_route import kb_router


def create_app():
    app = FastAPI(title="Server")

    app.include_router(kb_router)

    return app

