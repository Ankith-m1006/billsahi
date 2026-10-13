"""Model backends with one signature: llm(messages) -> text."""
import json
import time
import urllib.error
import urllib.request

from db import env


def ai_studio(model: str, temperature: float = 0.0):
    key = env("GEMINI_API_KEY")

    def call(messages):
        system = "\n".join(m["content"] for m in messages if m["role"] == "system")
        contents = [{"role": "user" if m["role"] == "user" else "model", "parts": [{"text": m["content"]}]}
                    for m in messages if m["role"] != "system"]
        if model.startswith("gemma"):  # Gemma on AI Studio takes no system_instruction
            contents[0]["parts"][0]["text"] = system + "\n\n" + contents[0]["parts"][0]["text"]
            body = {"contents": contents, "generationConfig": {"temperature": temperature}}
        else:
            body = {"contents": contents, "systemInstruction": {"parts": [{"text": system}]},
                    "generationConfig": {"temperature": temperature}}
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
        for i in range(5):
            try:
                req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=180) as r:
                    d = json.load(r)
                return "".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"] if not p.get("thought"))
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 503) and i < 4:
                    time.sleep(10 * (i + 1))
                    continue
                raise
    call.model_name = model
    return call


def tinker_model(base_model: str = "Qwen/Qwen3.5-4B", model_path: str | None = None, max_tokens: int = 64):
    """Base or fine-tuned model sampled on Tinker. Thinking is off: these are one-line JSON answers."""
    import os
    import tinker
    from tinker_cookbook import model_info, renderers
    from tinker_cookbook.tokenizer_utils import get_tokenizer

    os.environ.setdefault("TINKER_API_KEY", env("TINKER_API_KEY"))
    sc = tinker.ServiceClient()
    client = sc.create_sampling_client(model_path=model_path) if model_path else sc.create_sampling_client(base_model=base_model)
    tok = get_tokenizer(base_model)
    name = model_info.get_recommended_renderer_name(base_model)
    renderer = renderers.get_renderer(name + "_disable_thinking" if name == "qwen3_5" else name, tok)
    params = tinker.types.SamplingParams(max_tokens=max_tokens, temperature=0.0, stop=renderer.get_stop_sequences())

    def call(messages):
        prompt = renderer.build_generation_prompt(messages)
        res = client.sample(prompt=prompt, num_samples=1, sampling_params=params).result()
        msg, _ = renderer.parse_response(res.sequences[0].tokens)
        return renderers.get_text_content(msg)
    call.model_name = (model_path or base_model)
    return call
