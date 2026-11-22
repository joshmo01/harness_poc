"""Dev identity provider: user tokens, code-mode tokens and approval tokens.

Stands in for Yutrix SSO + AgentCore Identity (inbound auth) and the LOS
approval service. Tokens are HS256 JWTs signed with dev secrets.
"""
import time
import uuid

import jwt

from .config import APPROVAL_SECRET, IDP_SECRET

PERSONAS = {
    "underwriter": {"sub": "u-asha", "name": "Asha (Underwriter)", "role": "underwriter", "branch": "Pune"},
    "credit_manager": {"sub": "u-ravi", "name": "Ravi (Credit Manager)", "role": "credit_manager", "branch": "Pune"},
    "field_officer": {"sub": "u-imran", "name": "Imran (Field Officer)", "role": "field_officer", "branch": "Nashik"},
}

AGENT_ID = "uw-agent"  # registered agent client ID


def issue_user_token(persona: str, agent_id: str = AGENT_ID, ttl: int = 3600) -> str:
    p = PERSONAS[persona]
    now = int(time.time())
    claims = {**p, "agent_id": agent_id, "iat": now, "exp": now + ttl, "scope": "agent", "iss": "yutrix-dev-idp"}
    return jwt.encode(claims, IDP_SECRET, algorithm="HS256")


def issue_code_token(user_claims: dict, case_id: str, tools: list[str], ttl: int = 300) -> str:
    """Short-lived, down-scoped token for one Code Interpreter session."""
    now = int(time.time())
    claims = {k: user_claims[k] for k in ("sub", "name", "role", "branch", "agent_id")}
    claims.update({"iat": now, "exp": now + ttl, "scope": "code", "case_id": case_id,
                   "tools": tools, "iss": "yutrix-dev-idp", "jti": uuid.uuid4().hex})
    return jwt.encode(claims, IDP_SECRET, algorithm="HS256")


def verify_user_token(token: str) -> dict:
    return jwt.decode(token, IDP_SECRET, algorithms=["HS256"], issuer="yutrix-dev-idp")


def mint_approval_token(user_claims: dict, case_id: str, decision: str, ttl: int = 120) -> str:
    """Minted only by the BFF on a real button click (never by the agent)."""
    now = int(time.time())
    claims = {"sub": user_claims["sub"], "role": user_claims["role"], "case_id": case_id,
              "decision": decision, "iat": now, "exp": now + ttl, "jti": uuid.uuid4().hex,
              "iss": "yutrix-los-approvals"}
    return jwt.encode(claims, APPROVAL_SECRET, algorithm="HS256")


def verify_approval_token(token: str) -> dict:
    return jwt.decode(token, APPROVAL_SECRET, algorithms=["HS256"], issuer="yutrix-los-approvals")
