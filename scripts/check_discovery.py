import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import httpx
import time
from ui.server import app, MODEL_CACHE, ModelDiscoveryRequest, get_provider_models
from fastapi import HTTPException

@pytest.mark.asyncio
async def test_get_provider_models_caching():
    # Clear cache before test
    MODEL_CACHE.clear()
    
    provider_id = "openai"
    req = ModelDiscoveryRequest(api_key="test-key", base_url="https://api.openai.com/v1")
    
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "data": [
            {"id": "gpt-4"},
            {"id": "gpt-3.5-turbo"}
        ]
    }
    
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response
        # First call - should call httpx
        result = await get_provider_models(provider_id, req)
        assert result["success"] is True
        assert "gpt-4" in result["models"]
        assert "gpt-3.5-turbo" in result["models"]
        assert mock_get.call_count == 1
        
        # Second call - should use cache
        result2 = await get_provider_models(provider_id, req)
        assert result2 == result
        assert mock_get.call_count == 1  # Still 1
        
        # Call with different key - should bypass cache
        req_new = ModelDiscoveryRequest(api_key="new-key", base_url="https://api.openai.com/v1")
        result3 = await get_provider_models(provider_id, req_new)
        assert mock_get.call_count == 2
        
@pytest.mark.asyncio
async def test_get_provider_models_error_handling():
    MODEL_CACHE.clear()
    provider_id = "openai"
    req = ModelDiscoveryRequest(api_key="test-key", base_url="https://api.openai.com/v1")
    
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 401
    mock_response.text = "Unauthorized"
    
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response
        with pytest.raises(HTTPException) as excinfo:
            await get_provider_models(provider_id, req)
        assert excinfo.value.status_code == 401
        assert "Unauthorized" in excinfo.value.detail
