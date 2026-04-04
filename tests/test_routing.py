# tests/test_routing.py

import os
import pytest
from config.settings import _ensure_docker_host

def test_ensure_docker_host_with_localhost():
    # Setup
    os.environ["DOCKER_HOST_INTERNAL"] = "host.docker.internal"
    
    # Test localhost replacement
    assert _ensure_docker_host("http://localhost:11434") == "http://host.docker.internal:11434"
    assert _ensure_docker_host("http://127.0.0.1:11434") == "http://host.docker.internal:11434"

def test_ensure_docker_host_missing_protocol():
    # Setup
    os.environ["DOCKER_HOST_INTERNAL"] = "host.docker.internal"
    
    # Test adding http://
    assert _ensure_docker_host("localhost:11434") == "http://host.docker.internal:11434"
    assert _ensure_docker_host("127.0.0.1:11434") == "http://host.docker.internal:11434"
    assert _ensure_docker_host("host.docker.internal:11434") == "http://host.docker.internal:11434"

def test_ensure_docker_host_dynamic_ip():
    # Setup - simulate Linux bridge IP
    os.environ["DOCKER_HOST_INTERNAL"] = "172.17.0.1"
    
    assert _ensure_docker_host("http://localhost:11434") == "http://172.17.0.1:11434"
    assert _ensure_docker_host("127.0.0.1:11434") == "http://172.17.0.1:11434"

def test_ensure_docker_host_preserves_public_urls():
    assert _ensure_docker_host("https://api.openai.com/v1") == "https://api.openai.com/v1"
    assert _ensure_docker_host("https://api.anthropic.com") == "https://api.anthropic.com"

def test_ensure_docker_host_handles_trailing_slash():
    os.environ["DOCKER_HOST_INTERNAL"] = "host.docker.internal"
    assert _ensure_docker_host("http://localhost:11434/") == "http://host.docker.internal:11434/"
