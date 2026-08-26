"""Base evaluator class for rubric-based evaluation."""

from __future__ import annotations

from abc import ABC, abstractmethod
import json
import logging
import re
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


CONFIDENCE_EXTRACTION_PROMPT = """Extract the confidence score from the AI response below.
Find the line starting with "Confidence:" and return its integer value (0-100).

AI Response:
{prediction}

Return ONLY a JSON object: {{"confidence": <integer>}}
If no confidence line is found, return {{"confidence": 0}}."""


class Evaluator(ABC):
    _completer: Optional[Any] = None

    def set_completer(self, completer: Any) -> None:
        """Inject an async completer function to use for LLM calls."""
        self._completer = completer

    async def async_complete(self, messages: list, llm: str, max_tokens: int = 1024, **extra) -> str:
        """Call the injected completer, returning the response text."""
        if self._completer is not None:
            result = await self._completer(messages)
            return result["content"]
        try:
            import litellm
            body: Dict[str, Any] = {
                "model": llm,
                "messages": messages,
                "max_tokens": max_tokens,
            }
            body.update(extra)
            resp = await litellm.acompletion(**body)
            return resp.choices[0].message.content
        except ImportError:
            raise RuntimeError(
                "No completer set and litellm not installed. "
                "Call evaluator.set_completer() before compute_score()."
            )

    async def extract_confidence(self, prediction: str, llm: str = "gpt-4.1-mini") -> int:
        """Extract a structured 'Confidence: X%' from the prediction."""
        if not prediction or not prediction.strip():
            return 0
        prompt = CONFIDENCE_EXTRACTION_PROMPT.format(prediction=prediction)
        try:
            text = await self.async_complete(
                [{"role": "user", "content": prompt}], llm, max_tokens=32
            )
            json_cleaned = re.sub(r"^```json\s*|\s*```$", "", text.strip())
            parsed = json.loads(json_cleaned)
            confidence = parsed.get("confidence", 0)
            if isinstance(confidence, (int, float)):
                return max(0, min(100, int(confidence)))
        except Exception as e:
            logger.debug("Failed to extract confidence from prediction: %s", e)
        return 0

    @abstractmethod
    def build_prompt(self, prediction: str, item: Dict, **kwargs) -> str:
        raise NotImplementedError

    @abstractmethod
    def parse_response(self, judge_text: str) -> Dict:
        raise NotImplementedError

    @abstractmethod
    def default_response(self, err_msg: str = "") -> Dict:
        raise NotImplementedError

    @abstractmethod
    async def compute_score(self, prediction: str, item: Dict, **kwargs) -> Dict:
        raise NotImplementedError
