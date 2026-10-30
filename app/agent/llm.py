"""DeepSeek（OpenAI 兼容）客户端：仅用标准库，未配置 Key 时由上层降级为规则模式。"""
from __future__ import annotations

import json
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class LLMError(RuntimeError):
    """不包含密钥或完整请求体的安全错误。"""


def extract_json_object(text: str) -> dict:
    """从模型输出中提取第一个 JSON 对象；容忍 ```json 包裹与前后说明文字。"""
    if not text:
        return {}
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


class DeepSeekClient:
    """DeepSeek 官方 API（OpenAI 兼容 /chat/completions）。"""

    def __init__(
        self, api_key: Optional[str], base_url: str = "https://api.deepseek.com",
        model: str = "deepseek-chat", timeout_seconds: float = 25.0,
    ):
        self._api_key = (api_key or "").strip()
        self.base_url = (base_url or "https://api.deepseek.com").rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    @property
    def mode(self) -> str:
        return "deepseek" if self.available else "rules"

    def chat(
        self, messages: list[dict], *, temperature: float = 0.3, max_tokens: int = 800
    ) -> str:
        if not self.available:
            raise LLMError("未配置 DEEPSEEK_API_KEY")
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        request = Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": f"Bearer {self._api_key}",
                "User-Agent": "TravelAI/1.1",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise LLMError(f"DeepSeek 服务返回 HTTP {exc.code}") from None
        except (URLError, TimeoutError, OSError, UnicodeError, json.JSONDecodeError):
            raise LLMError("DeepSeek 服务暂时不可用") from None
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise LLMError("DeepSeek 返回了无法识别的响应") from None
        if not isinstance(content, str) or not content.strip():
            raise LLMError("DeepSeek 返回空内容")
        return content.strip()

    def chat_json(self, messages: list[dict], *, max_tokens: int = 400) -> dict:
        """意图解析：低温采样并要求纯 JSON，解析失败由规则解析兜底。"""
        return extract_json_object(
            self.chat(messages, temperature=0.0, max_tokens=max_tokens)
        )
