"""Pydantic schemas for the challenge server's launch/stop API."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ServiceInfo(BaseModel):
    service_name: str
    alias: str
    ip: str
    host: Optional[str] = None
    port: Optional[int] = None
    protocol: Optional[str] = None
    inner_host: Optional[str] = None
    inner_ip: Optional[str] = None
    inner_port: Optional[int] = None
    internal_port: Optional[int]
    external_host: Optional[str] = None
    external_port: Optional[int]


class LaunchResponse(BaseModel):
    status: str
    chal_id: str
    run_id: Optional[str] = None
    project_name: Optional[str] = None
    network_name: Optional[str] = None
    network_subnet: Optional[str] = None
    network_gateway: Optional[str] = None
    scoring: Dict[str, Any] = Field(default_factory=dict)
    debug: Dict[str, Any] = Field(default_factory=dict)
    services: List[ServiceInfo] = []


class StopResponse(BaseModel):
    status: str
    chal_id: str
    message: str
