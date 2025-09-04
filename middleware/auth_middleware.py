# import os
# import jwt
# from jwt.exceptions import ExpiredSignatureError, InvalidTokenError
# from fastapi import Request, HTTPException
# from fastapi.responses import JSONResponse
# from starlette.middleware.base import BaseHTTPMiddleware
# from fastapi.security.utils import get_authorization_scheme_param
# from starlette.responses import JSONResponse
# from opentelemetry import trace
 
# CORS_HEADERS = {
#     "Access-Control-Allow-Origin": "*",  # or specific origin
#     "Access-Control-Allow-Methods": "*",
#     "Access-Control-Allow-Headers": "*",
# }
 
# # Load Keycloak configuration from environment variables
# KEYCLOAK_PUBLIC_KEY = os.getenv("KEYCLOAK_PUBLIC_KEY")
# KEYCLOAK_ISSUER = os.getenv("KEYCLOAK_ISSUER")
# KEYCLOAK_AUDIENCE = os.getenv("KEYCLOAK_AUDIENCE")
# JWKS_URL = f"{KEYCLOAK_ISSUER}/protocol/openid-connect/certs"
# KEYCLOAK_ISSUER = os.getenv("KEYCLOAK_ISSUER")
 
# class KeycloakAuthMiddleware(BaseHTTPMiddleware):
#     """Middleware to enforce authentication and role-based authorization."""
 
#     async def dispatch(self, request: Request, call_next):
 
#         path = request.url.path
#         method = request.method
#         if method == "OPTIONS":
#             return JSONResponse(
#                 status_code=200,
#                 content={"message": "CORS preflight"},
#                 headers=CORS_HEADERS,
#             )
 
#         print("path print", path)
 
#         # Allow public paths (like homepage or docs) to bypass authentication
 
#         if (
#             path == "/"
#             or path.startswith("/process_directory")
#             or path.startswith("/query_project")
#             or path.startswith("/list_projects")
#             or path.startswith("/project_stats/{project_name}")
#             or path.startswith("/health")
#             or path.startswith("/process_documents")
#             or path.startswith("/generate_questions")
#             or path.startswith("/start_discovery")
#             or path.startswith("/process_transcript")
#             or path.startswith("/get_questions/{project_id}")
#             or path.startswith("/discovery_status/{project_id}")
#             or path.startswith("/generate_questions_by_id")
#             or path.startswith("/get_sow_data/{project_id}")
#             or path.startswith("/discovery_report/{project_id}")
#             or path.startswith("/process_additional_documents")
#             or path.startswith("/upload_additional_documents/{project_id}")
#             or path.startswith("/project_progress/{project_id}")
#             or path.startswith("/answers_by_source/{project_id}")
#             or path.startswith("/unanswered_questions/{project_id}")
#             or path.startswith("/bulk_process_additional_documents")
#         ):
 
#             return await call_next(request)
 
#         # Extract Authorization header
 
#         auth_header = request.headers.get("Authorization")
 
#         if not auth_header:
 
#             return JSONResponse(
#                 status_code=401,
#                 content={"detail": "Missing Authorization Header"},
#                 headers=CORS_HEADERS,
#             )
 
#         scheme, token = get_authorization_scheme_param(auth_header)
 
#         if scheme.lower() != "bearer" or not token:
 
#             return JSONResponse(
#                 status_code=401,
#                 content={"detail": "Invalid Authorization Header"},
#                 headers=CORS_HEADERS,
#             )
 
#         try:
 
#             # Convert literal '\n' into actual newlines
 
#             formatted_key = KEYCLOAK_PUBLIC_KEY.replace("\\n", "\n")
 
#             print("path_key", formatted_key)
 
#             payload = jwt.decode(
#                 token, formatted_key, algorithms=["RS256"], audience="account"
#             )
 
#             request.state.user = payload
#             # realm = payload.get("iss", "").split("/")[-1] if "iss" in payload else "unknown"
#             # client_id = payload.get("azp", "unknown-client")
#             user_id = payload.get("sub", "unknown-user")
#             username = payload.get("preferred_username", "anonymous")
#             span = trace.get_current_span()
#             span.set_attribute("user.id", user_id)
#             span.set_attribute("user.email", username)
#         except ExpiredSignatureError:
 
#             return JSONResponse(
#                 status_code=401,
#                 content={"detail": "Token has expired"},
#                 headers=CORS_HEADERS,
#             )
 
#         except InvalidTokenError:
 
#             return JSONResponse(
#                 status_code=401,
#                 content={"detail": "Invalid token"},
#                 headers=CORS_HEADERS,
#             )
 
#         # Authorization based on URL prefix
 
#         required_role = []
 
#         if path.startswith("/api/admin/"):
 
#             required_role = ["admin", "manager"]
 
#         elif path.startswith("/api/user/"):
 
#             required_role = ["user"]
 
#         if required_role:
 
#             roles = payload.get("realm_access", {}).get("roles", [])
 
#             if not any(role in roles for role in required_role):
 
#                 return JSONResponse(
#                     status_code=403,
#                     content={
#                         "detail": f"You don't have '{required_role}' access: Unauthorized"
#                     },
#                 )
           
 
#         return await call_next(request)
 

# import httpx
# import time
# import jwt
# from jwt.exceptions import ExpiredSignatureError, InvalidTokenError
# from fastapi import Request
# from fastapi.responses import JSONResponse
# from starlette.middleware.base import BaseHTTPMiddleware
# from fastapi.security.utils import get_authorization_scheme_param
# import logging, time
# from opentelemetry import trace
# logger = logging.getLogger("main")
# # In-memory cache for JWKS per issuer
# JWKS_CACHE = {}  # Key: issuer, Value: {"jwks_uri": ..., "fetched_at": timestamp}
# JWKS_TTL = 3600  # 1 hour
# class KeycloakAuthMiddleware(BaseHTTPMiddleware):
#     async def dispatch(self, request: Request, call_next):
#         start_time = time.time()
#         path = request.url.path
 
#         # Public paths allowed without auth
#         if (
#             path == "/" 
#             # path.startswith("/docs") or
#             # path.startswith("/openapi.json") or
#             # path.startswith("/api/user/session/agent/chat") or
#             # path.startswith("/api/admin/application/clients")
#         ):
#             response = await call_next(request)
#             duration = round(time.time() - start_time, 3)
#             logger.info("HTTP request",extra={
#                 "path": path,
#                 "method": request.method,
#                 "status_code": response.status_code,
#                 "response_time": duration,
#                 "realm": "public route no realm",
#                 "client_id": "public route no clientId",
#                 "username": "public route no username",
#                 "user_id": "public route no user_id",
#                 "ip": request.client.host
#             })
#             return response
 
#         auth_header = request.headers.get("Authorization")
#         if not auth_header:
#             return JSONResponse(status_code=401, content={"detail": "Missing Authorization Header"})
 
#         scheme, token = get_authorization_scheme_param(auth_header)
#         if scheme.lower() != "bearer" or not token:
#             return JSONResponse(status_code=401, content={"detail": "Invalid Authorization Header"})
 
#         try:
#             # Decode unverified token to extract issuer
#             unverified_payload = jwt.decode(token, options={"verify_signature": False})
#             issuer = unverified_payload.get("iss")
#             client_id = unverified_payload.get("azp")
#             if not issuer or not issuer.startswith("https://sso.yashtech.link/realms/"):
#                 return JSONResponse(status_code=403, content={"detail": "Untrusted token issuer"})
 
#             # Get JWKS URI for issuer (cached or fetched)
#             jwks_uri = await get_jwks_uri_for_issuer(issuer)
 
#             # Use PyJWKClient with JWKS URI to get signing key
#             jwk_client = jwt.PyJWKClient(jwks_uri)
#             signing_key = jwk_client.get_signing_key_from_jwt(token).key
 
#             # Fully decode and validate token
#             payload = jwt.decode(
#                 token,
#                 signing_key,
#                 algorithms=["RS256"],
#                 audience="account",  # Optionally: dynamically read expected audience
#                 issuer=issuer
#             )
#             request.state.user = payload
 
#         except ExpiredSignatureError:
#             return JSONResponse(status_code=401, content={"detail": "Token has expired"})
#         except InvalidTokenError:
#             return JSONResponse(status_code=401, content={"detail": "Invalid token"})
#         except Exception as e:
#             return JSONResponse(status_code=400, content={"detail": f"Token processing failed: {str(e)}"})
 
#         # Role-based access control
#         roles = payload.get("realm_access", {}).get("roles", [])
#         # if "/unified" in path:
#         #     return await call_next(request)
       
#         response = await call_next(request)
#         duration = round(time.time() - start_time, 3)
#          # Extract useful fields
#         realm = payload.get("iss", "").split("/")[-1] if "iss" in payload else "unknown"
#         client_id = payload.get("azp", "unknown-client")
#         user_id = payload.get("sub", "unknown-user")
#         username = payload.get("preferred_username", "anonymous")
#         span = trace.get_current_span()
#         span.set_attribute("user.id", user_id)
#         span.set_attribute("user.email", username)
#         logger.info("HTTP request",
#             extra={
#                 "path": path,
#                 "method": request.method,
#                 "status_code": response.status_code,
#                 "response_time": duration,
#                 "realm": realm,
#                 "client_id": client_id,
#                 "username": username,
#                 "user_id": user_id,
#                 "ip": request.client.host
#             })
#         return response
 
# # JWKS discovery and caching
# async def get_jwks_uri_for_issuer(issuer: str) -> str:
#     now = time.time()
#     cache = JWKS_CACHE.get(issuer)
 
#     if cache and now - cache["fetched_at"] < JWKS_TTL:
#         return cache["jwks_uri"]
 
#     async with httpx.AsyncClient() as client:
#         discovery_url = f"{issuer}/.well-known/openid-configuration"
#         discovery_res = await client.get(discovery_url)
#         if discovery_res.status_code != 200:
#             raise Exception(f"Failed to fetch OpenID config for issuer: {issuer}")
 
#         jwks_uri = discovery_res.json()["jwks_uri"]
 
#     JWKS_CACHE[issuer] = {"jwks_uri": jwks_uri, "fetched_at": now}
#     return jwks_uri
 

import httpx
import time
import jwt
from jwt.exceptions import ExpiredSignatureError, InvalidTokenError
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.security.utils import get_authorization_scheme_param
import logging
from opentelemetry import trace

logger = logging.getLogger("main")
# In-memory cache for JWKS per issuer
JWKS_CACHE = {}  # Key: issuer, Value: {"jwks_uri": ..., "fetched_at": timestamp}
JWKS_TTL = 3600  # 1 hour

class KeycloakAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):

        # --- THIS IS THE FIX ---
        # If the request method is OPTIONS, it's a CORS preflight request.
        # We must let it pass through without any authentication checks so that
        # the CORSMiddleware can handle it properly.
        if request.method == "OPTIONS":
            response = await call_next(request)
            return response
        # --- END OF FIX ---
        
        start_time = time.time()
        path = request.url.path
 
        # Public paths allowed without auth
        if (
            path == "/" or
            path.startswith("/docs") or
            path.startswith("/openapi.json") 
        ):
            response = await call_next(request)
            duration = round(time.time() - start_time, 3)
            logger.info("HTTP request",extra={
                "path": path,
                "method": request.method,
                "status_code": response.status_code,
                "response_time": duration,
                "realm": "public route no realm",
                "client_id": "public route no clientId",
                "username": "public route no username",
                "user_id": "public route no user_id",
                "ip": request.client.host
            })
            return response
 
        auth_header = request.headers.get("Authorization")
        if not auth_header:
            return JSONResponse(status_code=401, content={"detail": "Missing Authorization Header"})
 
        scheme, token = get_authorization_scheme_param(auth_header)
        if scheme.lower() != "bearer" or not token:
            return JSONResponse(status_code=401, content={"detail": "Invalid Authorization Header"})
 
        try:
            # Decode unverified token to extract issuer
            unverified_payload = jwt.decode(token, options={"verify_signature": False})
            issuer = unverified_payload.get("iss")
            if not issuer or not issuer.startswith("https://sso.yashtech.link/realms/"):
                return JSONResponse(status_code=403, content={"detail": "Untrusted token issuer"})
 
            # Get JWKS URI for issuer (cached or fetched)
            jwks_uri = await get_jwks_uri_for_issuer(issuer)
 
            # Use PyJWKClient with JWKS URI to get signing key
            jwk_client = jwt.PyJWKClient(jwks_uri)
            signing_key = jwk_client.get_signing_key_from_jwt(token).key
 
            # Fully decode and validate token
            payload = jwt.decode(
                token,
                signing_key,
                algorithms=["RS256"],
                audience="account",  # Optionally: dynamically read expected audience
                issuer=issuer
            )
            request.state.user = payload
 
        except ExpiredSignatureError:
            return JSONResponse(status_code=401, content={"detail": "Token has expired"})
        except InvalidTokenError:
            return JSONResponse(status_code=401, content={"detail": "Invalid token"})
        except Exception as e:
            return JSONResponse(status_code=400, content={"detail": f"Token processing failed: {str(e)}"})
 
        response = await call_next(request)
        duration = round(time.time() - start_time, 3)
        # Extract useful fields
        realm = payload.get("iss", "").split("/")[-1] if "iss" in payload else "unknown"
        client_id = payload.get("azp", "unknown-client")
        user_id = payload.get("sub", "unknown-user")
        username = payload.get("preferred_username", "anonymous")
        span = trace.get_current_span()
        span.set_attribute("user.id", user_id)
        span.set_attribute("user.email", username)
        logger.info("HTTP request",
            extra={
                "path": path,
                "method": request.method,
                "status_code": response.status_code,
                "response_time": duration,
                "realm": realm,
                "client_id": client_id,
                "username": username,
                "user_id": user_id,
                "ip": request.client.host
            })
        return response
 
# JWKS discovery and caching
async def get_jwks_uri_for_issuer(issuer: str) -> str:
    now = time.time()
    cache = JWKS_CACHE.get(issuer)
 
    if cache and now - cache["fetched_at"] < JWKS_TTL:
        return cache["jwks_uri"]
 
    async with httpx.AsyncClient() as client:
        discovery_url = f"{issuer}/.well-known/openid-configuration"
        discovery_res = await client.get(discovery_url)
        if discovery_res.status_code != 200:
            raise Exception(f"Failed to fetch OpenID config for issuer: {issuer}")
 
        jwks_uri = discovery_res.json()["jwks_uri"]
 
    JWKS_CACHE[issuer] = {"jwks_uri": jwks_uri, "fetched_at": now}
    return jwks_uri