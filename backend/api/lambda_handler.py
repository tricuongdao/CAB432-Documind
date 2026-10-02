"""AWS Lambda entrypoint (API Gateway HTTP API, payload format 2.0).

Mangum adapts the FastAPI ASGI app to the Lambda event/response contract.
Lifespan is disabled to keep cold starts short; all clients are lazily
initialised on first use inside the process.
"""

from mangum import Mangum

from app import app

handler = Mangum(app, lifespan="off")
