"""
REST API for approval queue.

Endpoints:
  GET  /api/approvals?status=pending      — list approvals (default: pending)
  GET  /api/approvals/{id}                — get one approval
  POST /api/approvals/{id}/approve        — approve a draft
  POST /api/approvals/{id}/reject         — reject a draft
  POST /api/approvals/{id}/edit-approve   — edit and approve a draft

Auth: Bearer token (tutor_api_token; falls back to gateway_auth_token).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel

from bridge.approvals import handler as approval_handler
from bridge.config import Settings, get_settings
from bridge.state import approvals

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/approvals")


# -- Auth ----------------------------------------------------------------------


def _verify_token(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    expected_token = settings.tutor_api_token or settings.gateway_auth_token
    expected = f"Bearer {expected_token}"
    if not authorization or authorization != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing Authorization header",
        )


# -- Endpoints -----------------------------------------------------------------


class EditApprovalRequest(BaseModel):
    text: str


@router.get(
    "",
    dependencies=[Depends(_verify_token)],
)
async def list_approvals(
    status_filter: str = "pending",
    booking_id: str | None = None,
    settings: Settings = Depends(get_settings),
) -> list[dict]:
    """List approvals, optionally filtered by status and booking_id."""
    all_pending = await approvals.list_pending(settings.state_path, booking_id)
    if status_filter == "pending":
        return all_pending
    # For non-pending filters, scan all files
    import json
    from pathlib import Path

    approvals_dir = Path(settings.state_path) / "approvals"
    if not approvals_dir.exists():
        return []

    results = []
    for path in sorted(approvals_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if status_filter and data.get("status") != status_filter:
                continue
            if booking_id and data.get("booking_id") != booking_id:
                continue
            results.append(data)
        except Exception:
            continue
    return results


@router.get(
    "/{approval_id}",
    dependencies=[Depends(_verify_token)],
)
async def get_approval(
    approval_id: str,
    settings: Settings = Depends(get_settings),
) -> dict:
    """Return one approval by ID."""
    data = await approvals.get_approval(settings.state_path, approval_id)
    if not data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Approval not found")
    return data


@router.post(
    "/{approval_id}/approve",
    dependencies=[Depends(_verify_token)],
)
async def approve_endpoint(
    approval_id: str,
    settings: Settings = Depends(get_settings),
) -> dict:
    """Approve a pending draft — sends it to the student."""
    data = await approvals.get_approval(settings.state_path, approval_id)
    if not data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Approval not found")
    if data.get("status") != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Approval already resolved")

    ok = await approval_handler.approve(approval_id, settings)
    if not ok:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to approve")

    return {"ok": True, "approval_id": approval_id, "status": "approved"}


@router.post(
    "/{approval_id}/reject",
    dependencies=[Depends(_verify_token)],
)
async def reject_endpoint(
    approval_id: str,
    settings: Settings = Depends(get_settings),
) -> dict:
    """Reject a pending draft — discards it."""
    data = await approvals.get_approval(settings.state_path, approval_id)
    if not data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Approval not found")
    if data.get("status") != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Approval already resolved")

    ok = await approval_handler.reject(approval_id, settings)
    if not ok:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to reject")

    return {"ok": True, "approval_id": approval_id, "status": "rejected"}


@router.post(
    "/{approval_id}/edit-approve",
    dependencies=[Depends(_verify_token)],
)
async def edit_approve_endpoint(
    approval_id: str,
    body: EditApprovalRequest,
    settings: Settings = Depends(get_settings),
) -> dict:
    """Edit a pending draft and send the edited version to the student."""
    data = await approvals.get_approval(settings.state_path, approval_id)
    if not data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Approval not found")
    if data.get("status") != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Approval already resolved")

    ok = await approval_handler.edit_and_approve(approval_id, body.text, settings)
    if not ok:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to edit and approve",
        )

    return {"ok": True, "approval_id": approval_id, "status": "edited"}
