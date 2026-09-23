import pytest

from opengrader.core.llm.dialects import get_dialect


@pytest.mark.parametrize("name", ["ollama", "openai-chat", "anthropic-messages"])
def test_system_instruction_is_separate_from_image_and_user_request(name: str) -> None:
    body = get_dialect(name).build_body(
        model="vision", system_prompt="Transcribe exactly.", prompt="Read this crop.", images=["iVBORw0KGgo"],
    )
    if name == "openai-chat":
        assert body["messages"][0] == {"role": "system", "content": "Transcribe exactly."}
        assert body["messages"][1]["role"] == "user"
        assert body["messages"][1]["content"][0] == {"type": "text", "text": "Read this crop."}
    else:
        assert body["system"] == "Transcribe exactly."
        if name == "ollama":
            assert body["prompt"] == "Read this crop."
            assert body["images"] == ["iVBORw0KGgo"]
        else:
            assert body["messages"][0]["content"][-1] == {"type": "text", "text": "Read this crop."}
