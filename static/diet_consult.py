# diet_consult.py
"""
Gemini-backed diet consultation helper.

Usage:
- Set GEMINI_API_KEY environment variable (Google AI Studio -> Get API key).
- Call `consult_profile(profile, totals, deficiencies, weather, lang='en')`
  where:
    profile = {"age": 30, "gender": "male", "height_cm": 175, "weight_kg": 70, "activity": "moderate"}
    totals = {"Protein": "6.36 g", "Calcium": "10.0 mg", ...}  (strings as returned by your existing API)
    deficiencies = {"Protein": "48.64 g", "Calcium": "1090.0 mg", ...}
    weather = {"condition": "Mist", "temp": 26.1, "humidity": 79}
- It returns a dict: {"summary": "...", "meal_plan": [...], "advice": "..."}
"""

import os
import re
from typing import Dict, Any, List
try:
    # recommended Google GenAI SDK (quickstart)
    from google import genai
except Exception as e:
    genai = None

# default model — change if you prefer another Gemini model
DEFAULT_MODEL = "gemini-2.5-flash"

def _ensure_client():
    """
    Initialize the GenAI client. Fails with RuntimeError if GEMINI_API_KEY not set
    or google-genai SDK not installed.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("Please set GEMINI_API_KEY environment variable (Google AI Studio).")
    if genai is None:
        raise RuntimeError("google-genai SDK not installed. Run: pip install google-genai")
    # client constructor will pick up API key from env automatically in most examples,
    # but set it explicitly to be safe:
    client = genai.Client(api_key=api_key)
    return client

def _format_prompt(profile: Dict[str, Any], totals: Dict[str, str],
                   deficiencies: Dict[str, str], weather: Dict[str, Any],
                   lang: str = "en") -> str:
    """
    Build a clear structured prompt for the model containing all relevant info.
    """
    lines = []
    lines.append("You are a friendly, evidence-based dietitian assistant.")
    lines.append("Produce a short personalized diet consultation in the requested language.")
    lines.append("")  # spacer
    # profile
    lines.append("USER PROFILE:")
    lines.append(f"- age: {profile.get('age','unknown')}")
    lines.append(f"- gender: {profile.get('gender','unknown')}")
    lines.append(f"- height_cm: {profile.get('height_cm','unknown')}")
    lines.append(f"- weight_kg: {profile.get('weight_kg','unknown')}")
    if profile.get("activity"):
        lines.append(f"- activity level: {profile.get('activity')}")
    lines.append("")  # spacer

    # weather
    if weather:
        lines.append("CURRENT WEATHER:")
        lines.append(f"- condition: {weather.get('condition')}")
        lines.append(f"- temp_c: {weather.get('temp')}")
        lines.append(f"- humidity: {weather.get('humidity')}")
        lines.append("")

    # totals
    lines.append("TOTAL NUTRIENTS (from provided foods):")
    if totals:
        for k, v in totals.items():
            lines.append(f"- {k}: {v}")
    else:
        lines.append("- (no nutrient totals provided)")
    lines.append("")

    # deficiencies
    lines.append("DEFICIENCIES (calculated):")
    if deficiencies:
        for k, v in deficiencies.items():
            lines.append(f"- {k}: need {v} more")
    else:
        lines.append("- (no deficiencies detected)")
    lines.append("")

    # instructions to model: desired structured output
    lines.append("TASK:")
    lines.append("1) Give a 2–3 sentence summary of the user's situation (health-safe).")
    lines.append("2) Provide a 3-meal sample meal plan for today (breakfast, lunch, dinner) with portions.")
    lines.append("3) For each deficient nutrient, list 1–2 food swaps or additions and approximate portion sizes.")
    lines.append("4) Provide brief general advice (hydration, timing, and any safety note).")
    lines.append("5) Output in JSON only (no extra text) with keys: summary (string), meal_plan (list of {meal,name,items}), advice (string).")
    # language hint
    if lang and lang != "en":
        lines.append(f"Respond in the following language: {lang}")
    lines.append("")
    lines.append("JSON format example:")
    lines.append('{"summary":"...", "meal_plan":[{"meal":"Breakfast","name":"Oats bowl","items":["..."]}], "advice":"..."}')
    lines.append("")  # final
    return "\n".join(lines)

def _parse_json_like(text: str) -> Dict[str, Any]:
    """
    The model is instructed to return JSON only — but be defensive.
    Try to find JSON substring and parse it.
    """
    import json
    try:
        # find the first { ... } block
        m = re.search(r"\{.*\}", text, flags=re.S)
        if m:
            payload = m.group(0)
            return json.loads(payload)
    except Exception:
        pass
    # fallback: return raw text under summary
    return {"summary": text.strip(), "meal_plan": [], "advice": ""}

def consult_profile(profile: Dict[str, Any], totals: Dict[str, str],
                    deficiencies: Dict[str, str], weather: Dict[str, Any],
                    lang: str = "en", model: str = DEFAULT_MODEL) -> Dict[str, Any]:
    """
    Main entrypoint.

    Returns a dict with keys: summary, meal_plan (list), advice, raw (full model text)
    """
    client = _ensure_client()
    prompt = _format_prompt(profile, totals, deficiencies, weather, lang=lang)
    # The SDK's generate_content pattern from quickstart:
    resp = client.models.generate_content(model=model, contents=prompt)
    # `resp.text` contains generated text (per quickstart sample)
    raw_text = ""
    try:
        raw_text = resp.text if hasattr(resp, "text") else str(resp)
    except Exception:
        raw_text = str(resp)
    parsed = _parse_json_like(raw_text)
    # normalize result
    return {
        "summary": parsed.get("summary", "") or "",
        "meal_plan": parsed.get("meal_plan", []) or [],
        "advice": parsed.get("advice", "") or "",
        "raw": raw_text
    }

# If run directly, quick demo using dummy values
if __name__ == "__main__":
    # Demo - only for local testing; requires GEMINI_API_KEY set.
    demo_profile = {"age": 28, "gender": "male", "height_cm": 175, "weight_kg": 72, "activity": "moderate"}
    demo_totals = {"Protein": "6.36 g", "Calcium": "10.0 mg", "Fiber": "5.8 g", "Iron": "0.89 mg", "Vitamin C": "0.2 mg"}
    demo_def = {"Protein": "48.64 g", "Calcium": "1090.0 mg", "Fiber": "27.2 g", "Iron": "7.91 mg", "Vitamin C": "98.8 mg"}
    demo_weather = {"condition":"Mist","temp":26.1,"humidity":79}
    out = consult_profile(demo_profile, demo_totals, demo_def, demo_weather, lang="en")
    import json
    print(json.dumps(out, indent=2, ensure_ascii=False))
