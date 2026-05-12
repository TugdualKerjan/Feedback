from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api import router as api_router
from proxy import router as proxy_router

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

app.include_router(api_router)
app.include_router(proxy_router)
