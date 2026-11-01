from app.core.config import Settings
from app.schemas.harness import GenerationRequestCreate

def test_settings_codegen_mode_default():
    s = Settings()
    assert s.codegen_mode in ("hybrid", "llm")

def test_generation_request_create_codegen_mode():
    req = GenerationRequestCreate(codegen_mode="llm")
    assert req.codegen_mode == "llm"
    req_default = GenerationRequestCreate()
    assert req_default.codegen_mode is None

def test_generation_request_create_puck_json_is_optional():
    assert GenerationRequestCreate().puck_json is None
    assert GenerationRequestCreate(puck_json={"content": []}, codegen_mode="hybrid").puck_json == {"content": []}
