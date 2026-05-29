import json
import re
import anthropic
from loguru import logger
from config.settings import settings
from prompts.claude_prompts import (
    CLASSIFICATION_SYSTEM, CLASSIFICATION_USER,
    MESSAGE_SYSTEM, MESSAGE_USER, PERSONA_ANGLES,
    WEBSITE_SUMMARY_SYSTEM, WEBSITE_SUMMARY_USER,
)

client = anthropic.Anthropic(api_key=settings.anthropic_api_key)


# ─── Helpers ──────────────────────────────────────────────────

def _safe_truncate(text: str | None, max_chars: int = 1500) -> str:
    if not text:
        return "Not provided"
    return text[:max_chars] + "..." if len(text) > max_chars else text


def _extract_json(text: str) -> dict:
    """Pull the first JSON object out of a Claude response."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON found in response: {text[:200]}")
    return json.loads(match.group())


# ─── Website summariser ───────────────────────────────────────

def summarise_website(url: str, content: str) -> str:
    """Summarise scraped website content for personalisation."""
    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=300,
            system=WEBSITE_SUMMARY_SYSTEM,
            messages=[{
                "role": "user",
                "content": WEBSITE_SUMMARY_USER.format(
                    url=url,
                    content=_safe_truncate(content, 3000),
                ),
            }],
        )
        return response.content[0].text.strip()
    except Exception as e:
        logger.warning(f"Website summary failed for {url}: {e}")
        return "No useful content found."


# ─── Profile classifier ───────────────────────────────────────

def classify_profile(profile_data: dict) -> dict:
    """
    Classify a LinkedIn profile into a persona and score it.

    Returns:
        {
            "persona": str,
            "match_score": float,
            "classification_notes": str,
            "personalisation_hooks": list[str],
        }
    """
    try:
        prompt = CLASSIFICATION_USER.format(
            full_name=profile_data.get("full_name", "Unknown"),
            headline=_safe_truncate(profile_data.get("headline"), 200),
            current_role=profile_data.get("current_role", "Unknown"),
            current_company=profile_data.get("current_company", "Unknown"),
            location=profile_data.get("location", "Unknown"),
            about=_safe_truncate(profile_data.get("about"), 1000),
            skills=_safe_truncate(profile_data.get("skills"), 300),
            website_content=_safe_truncate(profile_data.get("website_content"), 500),
        )

        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=600,
            system=CLASSIFICATION_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )

        result = _extract_json(response.content[0].text)

        # Validate persona key
        valid_personas = [
            "senior_designer", "design_leader", "recruiter", "hr",
            "startup_founder", "product_manager", "fintech_professional", "unknown",
        ]
        if result.get("persona") not in valid_personas:
            result["persona"] = "unknown"

        result["match_score"] = float(result.get("match_score", 0))
        result["personalisation_hooks"] = result.get("personalisation_hooks", [])

        logger.info(
            f"Classified {profile_data.get('full_name')} → "
            f"{result['persona']} (score: {result['match_score']})"
        )
        return result

    except Exception as e:
        logger.error(f"Classification failed: {e}")
        return {
            "persona": "unknown",
            "match_score": 0.0,
            "classification_notes": f"Classification error: {str(e)}",
            "personalisation_hooks": [],
        }


# ─── Message generator ────────────────────────────────────────

def generate_message(profile_data: dict, classification: dict) -> dict:
    """
    Generate a personalised LinkedIn message for a newly connected person.

    Returns:
        {
            "message": str,
            "reasoning": str,
            "char_count": int,
        }
    """
    persona_key = classification.get("persona", "unknown")
    angle_info = PERSONA_ANGLES.get(persona_key, PERSONA_ANGLES["unknown"])

    hooks = classification.get("personalisation_hooks", [])
    hooks_text = "\n".join(f"- {h}" for h in hooks) if hooks else "- No specific hooks found"

    try:
        system_prompt = MESSAGE_SYSTEM.format(
            your_name=settings.your_name,
            your_role=settings.your_role,
            your_experience_years=settings.your_experience_years,
        )

        user_prompt = MESSAGE_USER.format(
            your_name=settings.your_name,
            full_name=profile_data.get("full_name", "there"),
            current_role=profile_data.get("current_role", "their role"),
            current_company=profile_data.get("current_company", "their company"),
            persona=persona_key.replace("_", " ").title(),
            skills=_safe_truncate(profile_data.get("skills"), 200),
            hooks=hooks_text,
            angle=angle_info["angle"],
            cta=angle_info["cta"],
        )

        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=400,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )

        message_text = response.content[0].text.strip()
        # Strip any quotes Claude might add
        message_text = message_text.strip('"').strip("'")

        logger.info(
            f"Generated message for {profile_data.get('full_name')} "
            f"({len(message_text)} chars)"
        )

        return {
            "message": message_text,
            "reasoning": f"Persona: {persona_key} | Angle: {angle_info['angle'][:80]}",
            "char_count": len(message_text),
        }

    except Exception as e:
        logger.error(f"Message generation failed: {e}")
        return {
            "message": "",
            "reasoning": f"Generation error: {str(e)}",
            "char_count": 0,
        }
