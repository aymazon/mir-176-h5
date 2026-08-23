"""FastAPI application entrypoint.

Routes are intentionally NOT registered here yet — REST API lands in todo 3 (auth/character)
and SSE stream in todo 5. Keep this a bare app factory/instance.
"""

from fastapi import FastAPI

app = FastAPI(title="mir-176-h5", version="0.1.0")
