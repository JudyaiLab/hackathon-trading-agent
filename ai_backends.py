"""
AI backend call functions for OpusAnalyst.

Extracted from opus_analyst.py for code quality (file size < 800 lines).
No logic changes — pure extraction.

Each function takes the necessary clients/config as parameters and returns
(raw_text, model_name, input_tokens, output_tokens) or None on failure.
"""
import logging
import os
import subprocess

from ai_prompts import ANALYST_SYSTEM_PROMPT, POSITION_REVIEW_PROMPT

logger = logging.getLogger(__name__)

# Re-import constants from opus_analyst to avoid circular deps
MINIMAX_MODEL = "MiniMax-M2.7"
OLLAMA_MODEL = "qwen2.5:7b"
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MAX_TOKENS = 1024
ANALYSIS_TIMEOUT = 45
OLLAMA_TIMEOUT = 30


def call_claude_subscription(context: str, pair: str,
                             claude_subscription: bool) -> tuple | None:
    """Call Claude Opus via subscription CLI (claude -p). No API credits.

    Returns (raw_text, model, input_tokens, output_tokens) or None.
    """
    if not claude_subscription:
        return None

    prompt = f"{ANALYST_SYSTEM_PROMPT}\n\n{context}"
    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--output-format", "text"],
            capture_output=True, text=True,
            timeout=ANALYSIS_TIMEOUT,
            env={**os.environ, "ANTHROPIC_API_KEY": ""},  # force subscription
        )
        if result.returncode != 0:
            logger.warning(f"claude -p failed for {pair}: {result.stderr[:200]}")
            return None

        raw_text = result.stdout.strip()
        if not raw_text:
            logger.warning(f"claude -p empty response for {pair}")
            return None

        # Estimate tokens (rough: 4 chars per token)
        input_tokens = len(prompt) // 4
        output_tokens = len(raw_text) // 4
        return (raw_text, "claude-opus-subscription", input_tokens, output_tokens)

    except subprocess.TimeoutExpired:
        logger.warning(f"claude -p timeout for {pair}")
        return None
    except Exception as e:
        logger.error(f"claude -p error for {pair}: {e}")
        return None


def call_anthropic(context: str, pair: str,
                   anthropic_client, model: str,
                   chart_image_b64: str | None = None) -> tuple | None:
    """Call Anthropic API. Returns (raw_text, model, input_tokens, output_tokens) or None."""
    if not anthropic_client:
        return None

    import anthropic

    messages = []
    content_parts = [{"type": "text", "text": context}]

    if chart_image_b64:
        content_parts.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": chart_image_b64,
            },
        })
        content_parts.append({
            "type": "text",
            "text": "Above is the 4H candlestick chart for this pair with EMA 20/50/100/200. Please include chart pattern recognition (Head & Shoulders, Double Top, Flag, Wedge, etc.) in your analysis.",
        })

    messages.append({"role": "user", "content": content_parts})

    try:
        response = anthropic_client.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            system=ANALYST_SYSTEM_PROMPT,
            messages=messages,
            timeout=ANALYSIS_TIMEOUT,
        )
        if not response.content:
            logger.error(f"Anthropic API returned empty content for {pair}")
            return None
        raw_text = response.content[0].text.strip()
        if not raw_text:
            logger.warning(f"Anthropic API returned empty text for {pair}")
            return None
        return (raw_text, model,
                response.usage.input_tokens, response.usage.output_tokens)

    except anthropic.BadRequestError as e:
        if "credit balance" in str(e):
            logger.warning(f"Anthropic credits exhausted, switching to Groq for {pair}")
            return None
        logger.error(f"Anthropic error for {pair}: {e}")
        return None

    except anthropic.APITimeoutError:
        logger.warning(f"Anthropic timeout for {pair}")
        return None

    except anthropic.APIError as e:
        logger.error(f"Anthropic API error for {pair}: {e}")
        return None

    except Exception as e:
        logger.error(f"Anthropic call failed for {pair}: {e}")
        return None


def call_minimax(context: str, pair: str, minimax_client) -> tuple | None:
    """Call MiniMax M2.7 via Anthropic-compatible API (text-only, with thinking).

    M2.7 returns ThinkingBlock + TextBlock when thinking is enabled.
    We iterate content blocks to extract the TextBlock text.

    Returns (raw_text, model, input_tokens, output_tokens) or None.
    """
    if not minimax_client:
        return None

    import anthropic

    try:
        # M2.7 thinking mode consumes tokens from max_tokens budget,
        # so we need a higher limit (4096) to fit both thinking + text output
        response = minimax_client.messages.create(
            model=MINIMAX_MODEL,
            max_tokens=4096,
            system=ANALYST_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": context}],
            timeout=ANALYSIS_TIMEOUT,  # Use consistent timeout
        )

        if not response.content:
            logger.error(f"MiniMax API returned empty content for {pair}")
            return None

        # M2.7 with thinking returns [ThinkingBlock, TextBlock]
        # Extract the TextBlock text (skip thinking blocks)
        raw_text = ""
        for block in response.content:
            if getattr(block, "type", "") == "text":
                raw_text = block.text.strip()
                break

        if not raw_text:
            logger.warning(f"MiniMax API returned no text block for {pair}")
            return None

        logger.info(f"MiniMax M2.7 analysis for {pair}: {len(raw_text)} chars")
        return (raw_text, f"minimax/{MINIMAX_MODEL}",
                response.usage.input_tokens, response.usage.output_tokens)

    except anthropic.BadRequestError as e:
        logger.error(f"MiniMax bad request for {pair}: {e}")
        return None

    except anthropic.APITimeoutError:
        logger.warning(f"MiniMax timeout for {pair}")
        return None

    except anthropic.APIError as e:
        logger.error(f"MiniMax API error for {pair}: {e}")
        return None

    except Exception as e:
        logger.error(f"MiniMax call failed for {pair}: {e}")
        return None


def call_ollama(context: str, pair: str, ollama_available: bool) -> tuple | None:
    """Call local Ollama model. Returns (raw_text, model, input_tokens, output_tokens) or None."""
    if not ollama_available:
        return None

    import requests

    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "messages": [
                    {"role": "system", "content": ANALYST_SYSTEM_PROMPT},
                    {"role": "user", "content": context},
                ],
                "stream": False,
                "options": {
                    "temperature": 0.3,
                    "num_predict": MAX_TOKENS,
                },
            },
            timeout=OLLAMA_TIMEOUT,
        )

        if resp.status_code != 200:
            logger.error(f"Ollama error for {pair}: {resp.status_code} {resp.text[:200]}")
            return None

        data = resp.json()
        if not isinstance(data, dict):
            logger.error(f"Ollama malformed response for {pair}")
            return None

        raw_text = data.get("message", {}).get("content", "").strip()
        if not raw_text:
            logger.error(f"Ollama empty content for {pair}")
            return None

        # Ollama returns eval_count (output tokens) and prompt_eval_count (input tokens)
        prompt_tokens = data.get("prompt_eval_count", 0)
        eval_tokens = data.get("eval_count", 0)

        logger.info(f"Ollama {OLLAMA_MODEL} analysis for {pair}: {len(raw_text)} chars")
        return (raw_text, f"ollama/{OLLAMA_MODEL}",
                prompt_tokens, eval_tokens)

    except Exception as e:
        logger.error(f"Ollama call failed for {pair}: {e}")
        return None


def call_groq(context: str, pair: str, groq_api_key: str) -> tuple | None:
    """Call Groq API as fallback. Returns (raw_text, model, input_tokens, output_tokens) or None."""
    if not groq_api_key:
        return None

    import requests

    try:
        resp = requests.post(
            GROQ_URL,
            headers={
                "Authorization": f"Bearer {groq_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": GROQ_MODEL,
                "messages": [
                    {"role": "system", "content": ANALYST_SYSTEM_PROMPT},
                    {"role": "user", "content": context},
                ],
                "max_tokens": MAX_TOKENS,
                "temperature": 0.3,
            },
            timeout=ANALYSIS_TIMEOUT,
        )

        if resp.status_code != 200:
            logger.error(f"Groq API error for {pair}: {resp.status_code} {resp.text[:200]}")
            return None

        data = resp.json()

        # Validate response structure
        if not isinstance(data, dict) or "choices" not in data or not data["choices"]:
            logger.error(f"Groq API malformed response for {pair}: {str(data)[:200]}")
            return None

        choice = data["choices"][0]
        if not isinstance(choice, dict) or "message" not in choice:
            logger.error(f"Groq API missing message in choice for {pair}")
            return None

        raw_text = choice["message"].get("content", "").strip()
        if not raw_text:
            logger.error(f"Groq API empty content for {pair}")
            return None

        usage = data.get("usage", {})
        logger.info(f"Groq analysis for {pair}: {len(raw_text)} chars")
        return (raw_text, f"groq/{GROQ_MODEL}",
                usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))

    except Exception as e:
        logger.error(f"Groq call failed for {pair}: {e}")
        return None


def call_minimax_position(context: str, pair: str, minimax_client) -> tuple | None:
    """Call MiniMax for position review (separate from signal analysis)."""
    if not minimax_client:
        return None

    import anthropic

    try:
        response = minimax_client.messages.create(
            model=MINIMAX_MODEL,
            max_tokens=2048,
            system=POSITION_REVIEW_PROMPT,
            messages=[{"role": "user", "content": context}],
            timeout=ANALYSIS_TIMEOUT,
        )

        if not response.content:
            return None

        raw_text = ""
        for block in response.content:
            if getattr(block, "type", "") == "text":
                raw_text = block.text.strip()
                break

        if not raw_text:
            return None

        return (raw_text, f"minimax/{MINIMAX_MODEL}",
                response.usage.input_tokens, response.usage.output_tokens)

    except Exception as e:
        logger.error(f"MiniMax position review failed for {pair}: {e}")
        return None
