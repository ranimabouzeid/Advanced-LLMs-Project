"""Dependency lookup only; session behavior lives in app.sessions."""

from fastapi import Request

from app.sessions import SessionCoordinator


def get_coordinator(request: Request) -> SessionCoordinator:
    return request.app.state.coordinator
