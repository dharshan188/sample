# app.py
import os
import re
import json
from typing import Dict, Any
from flask import Flask, render_template, request, jsonify
import requests

# dotenv: load .env if present (so systemd/env files still work too)
from dotenv import load_dotenv
load_dotenv()

# Try to import the Google GenAI SDK (Gemini). We will fail gracefully if not installed.
try:
    from google import genai  # google-genai SDK
except Exception:
    genai = None

app = Flask(__name__)

# ----------------- API KEYS (from environment) -----------------
USDA_API_KEY = os.getenv("USDA_API_KEY")
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")  # your Gemini (Google) API key

if not USDA_API_KEY or not WEATHER_API_KEY:
    # we raise here because analyze route depends on these. For local dev you must set these.
    raise RuntimeError("Please set USDA_API_KEY and WEATHER_API_KEY environment variables (in .env or system env).")

# ----------------- WEATHER DATA -----------------
def get_weather(city: str):
    try:
        url = "http://api.weatherapi.com/v1/current.json"
        params = {"key": WEATHER_API_KEY, "q": city, "aqi": "no"}
        r = requests.get(url, params=params, timeout=8)
        r.raise_for_status()
        d = r.json()
        return {
            "condition": d["current"]["condition"]["text"],
            "temp": d["current"]["temp_c"],
            "humidity": d["current"]["humidity"],
        }
    except Exception:
        return None

# ----------------- NUTRIENTS FETCH -----------------
def get_food_nutrients(food: str) -> Dict[str, tuple]:
    url = "https://api.nal.usda.gov/fdc/v1/foods/search"
    params = {"api_key": USDA_API_KEY, "query": food, "pageSize": 1}
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        nutrients = {}
        foods = data.get("foods", [])
        if not foods:
            return nutrients
        food_data = foods[0]
        for n in food_data.get("foodNutrients", []):
            name = n.get("nutrientName") or n.get("name")
            val = n.get("value")
            unit = n.get("unitName") or n.get("unit")
            if name and val is not None:
                try:
                    nutrients[name.strip()] = (float(val), unit or "")
                except Exception:
                    # ignore parsing problems
                    continue
        return nutrients
    except Exception:
        return {}

def convert_to_mg(amount: float, unit: str) -> float:
    unit = (unit or "").lower()
    if unit in ("g", "gram", "grams"):
        return amount * 1000.0
    if unit in ("mg", "milligram", "milligrams"):
        return amount
    return amount

NUTRIENT_KEY_MAP = {
    "Protein": ["protein"],
    "Vitamin C": ["vitamin c", "ascorbic acid"],
    "Iron": ["iron"],
    "Calcium": ["calcium"],
    "Fiber": ["fiber", "dietary fiber"],
}

def calculate_deficiency(total_nutrients_mg: Dict[str,float], gender: str, height_cm: float, weight_kg: float):
    baseline = {
        "Protein_g": 50.0,
        "Vitamin C_mg": 90.0,
        "Iron_mg": 18.0 if gender.lower() == "female" else 8.0,
        "Calcium_mg": 1000.0,
        "Fiber_g": 30.0,
    }
    bmi = weight_kg / ((height_cm / 100.0) ** 2) if height_cm > 0 else 0
    if bmi and bmi < 18.5:
        for k in list(baseline.keys()):
            baseline[k] *= 1.10
    elif bmi and bmi > 25:
        for k in list(baseline.keys()):
            baseline[k] *= 0.90

    deficiencies = {}
    protein_mg = total_nutrients_mg.get("Protein", 0.0)
    fiber_mg = total_nutrients_mg.get("Fiber", 0.0)
    if protein_mg < (baseline["Protein_g"] * 1000.0) * 0.6:
        need_mg = baseline["Protein_g"] * 1000.0 - protein_mg
        deficiencies["Protein"] = f"{round(need_mg/1000.0, 2)} g"
    if fiber_mg < (baseline["Fiber_g"] * 1000.0) * 0.6:
        need_mg = baseline["Fiber_g"] * 1000.0 - fiber_mg
        deficiencies["Fiber"] = f"{round(need_mg/1000.0, 2)} g"

    for short_key, base_key in [("Vitamin C", "Vitamin C_mg"), ("Iron", "Iron_mg"), ("Calcium", "Calcium_mg")]:
        have = total_nutrients_mg.get(short_key, 0.0)
        need = baseline[base_key]
        if have < need * 0.6:
            need_more = need - have
            deficiencies[short_key] = f"{round(need_more, 2)} mg"
    return deficiencies

def recommend_foods(defic: Dict[str,str], weather: Dict[str,Any]):
    base = {
        "Protein": [("Chicken", "27 g"), ("Eggs", "13 g"), ("Paneer", "18 g")],
        "Iron": [("Spinach", "2.7 mg"), ("Liver", "6.5 mg"), ("Beans", "3.7 mg")],
        "Calcium": [("Milk", "120 mg"), ("Curd", "80 mg"), ("Almonds", "75 mg")],
        "Fiber": [("Oats", "10 g"), ("Apple", "4.5 g"), ("Carrots", "3 g")],
        "Vitamin C": [("Orange", "53 mg"), ("Guava", "200 mg"), ("Kiwi", "90 mg")],
    }
    temp_foods = ["Cucumber", "Yogurt"] if weather and weather.get("temp", 0) > 30 else ["Soup", "Eggs"]
    rec = []
    for n in defic.keys():
        rec.extend(base.get(n, []))
    for f in temp_foods:
        rec.append((f, "-", "-"))
    return rec[:10]

# ----------------- Gemini helper (in-file) -----------------
DEFAULT_GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

def _ensure_gemini_client():
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY not set in environment (.env or system).")
    if genai is None:
        raise RuntimeError("google-genai SDK not installed (pip install google-genai).")
    # Initialize client explicitly
    client = genai.Client(api_key=GEMINI_API_KEY)
    return client

def _format_gemini_prompt(profile: Dict[str,Any], totals: Dict[str,str],
                          deficiencies: Dict[str,str], weather: Dict[str,Any],
                          lang: str="en") -> str:
    lines = []
    lines.append("You are a professional, evidence-based dietitian assistant.")
    lines.append("Produce a short personalized diet consultation in the requested language.")
    lines.append("")
    lines.append("USER PROFILE:")
    lines.append(f"- age: {profile.get('age','unknown')}")
    lines.append(f"- gender: {profile.get('gender','unknown')}")
    lines.append(f"- height_cm: {profile.get('height_cm','unknown')}")
    lines.append(f"- weight_kg: {profile.get('weight_kg','unknown')}")
    if profile.get("activity"):
        lines.append(f"- activity level: {profile.get('activity')}")
    lines.append("")
    if weather:
        lines.append("CURRENT WEATHER:")
        lines.append(f"- condition: {weather.get('condition')}")
        lines.append(f"- temp_c: {weather.get('temp')}")
        lines.append(f"- humidity: {weather.get('humidity')}")
        lines.append("")
    lines.append("TOTAL NUTRIENTS (from provided foods):")
    if totals:
        for k,v in totals.items():
            lines.append(f"- {k}: {v}")
    else:
        lines.append("- (no nutrient totals provided)")
    lines.append("")
    lines.append("DEFICIENCIES (calculated):")
    if deficiencies:
        for k,v in deficiencies.items():
            lines.append(f"- {k}: need {v} more")
    else:
        lines.append("- (no deficiencies detected)")
    lines.append("")
    lines.append("TASK:")
    lines.append("1) Give a 2–3 sentence summary of the user's situation.")
    lines.append("2) Provide a 3-meal sample meal plan for today (breakfast, lunch, dinner) with portions.")
    lines.append("3) For each deficient nutrient, list 1–2 food swaps or additions and approximate portion sizes.")
    lines.append("4) Provide brief general advice (hydration, timing, and any safety note).")
    lines.append("5) Output in JSON only with keys: summary (string), meal_plan (list of {meal,name,items}), advice (string).")
    if lang and lang != "en":
        lines.append(f"Respond in the following language: {lang}")
    lines.append("")
    lines.append('Return JSON only. Example:')
    lines.append('{"summary":"...", "meal_plan":[{"meal":"Breakfast","name":"Oats bowl","items":["..."]}], "advice":"..."}')
    return "\n".join(lines)

def _extract_json_from_text(text: str) -> Dict[str,Any]:
    try:
        m = re.search(r"\{.*\}", text, flags=re.S)
        if m:
            return json.loads(m.group(0))
    except Exception:
        pass
    return {"summary": text.strip(), "meal_plan": [], "advice": ""}

def consult_with_gemini(profile: Dict[str,Any], totals: Dict[str,str],
                        deficiencies: Dict[str,str], weather: Dict[str,Any], lang: str="en", model: str=DEFAULT_GEMINI_MODEL) -> Dict[str,Any]:
    client = _ensure_gemini_client()
    prompt = _format_gemini_prompt(profile, totals, deficiencies, weather, lang=lang)
    # call generate_content as in SDK quickstart
    resp = client.models.generate_content(model=model, contents=prompt)
    raw_text = ""
    try:
        raw_text = resp.text if hasattr(resp, "text") else str(resp)
    except Exception:
        raw_text = str(resp)
    parsed = _extract_json_from_text(raw_text)
    return {"summary": parsed.get("summary",""), "meal_plan": parsed.get("meal_plan",[]), "advice": parsed.get("advice",""), "raw": raw_text}

# ----------------- ROUTES -----------------
@app.route("/")
def home():
    return render_template("nutri.html")

@app.route("/analyze", methods=["POST"])
def analyze():
    data = request.get_json() or {}
    city = (data.get("city") or "").strip()
    items = data.get("items", [])
    gender = data.get("gender", "male")
    try:
        height = float(data.get("height") or 0)
    except Exception:
        height = 0.0
    try:
        weight = float(data.get("weight") or 0)
    except Exception:
        weight = 0.0

    if not city:
        return jsonify({"error": "City required"}), 400

    weather = get_weather(city)
    if not weather:
        return jsonify({"error": f"Weather data not found for city: {city}"}), 404

    totals_mg = {}
    # items: list of {name, qty (grams)}
    for it in items:
        name = (it.get("name") or "").strip()
        try:
            qty_g = float(it.get("qty") or 0)
        except Exception:
            qty_g = 0.0
        if not name or qty_g <= 0:
            continue
        nut = get_food_nutrients(name)
        for full_name, (val, unit) in nut.items():
            try:
                actual = float(val) * (qty_g / 100.0)  # convert per-100g nutrient
            except Exception:
                continue
            matched_key = None
            low = full_name.lower()
            for friendly, substrings in NUTRIENT_KEY_MAP.items():
                if any(s in low for s in substrings):
                    matched_key = friendly
                    break
            if not matched_key:
                continue
            amount_mg = convert_to_mg(actual, unit)
            totals_mg[matched_key] = totals_mg.get(matched_key, 0.0) + amount_mg

    defic = calculate_deficiency(totals_mg, gender, height, weight)
    rec = recommend_foods(defic, weather)

    human_totals = {}
    for k, v in totals_mg.items():
        if k in ("Protein", "Fiber"):
            human_totals[k] = f"{round(v/1000.0, 2)} g"
        else:
            human_totals[k] = f"{round(v, 2)} mg"

    return jsonify({
        "weather": weather,
        "total_nutrients": human_totals,
        "deficient": defic,
        "recommendations": rec
    })

@app.route("/consult", methods=["POST"])
def consult():
    """
    Accepts JSON with profile + totals + deficient + weather + lang (same as frontend).
    If Gemini is not configured, returns an error explaining what's missing.
    """
    data = request.get_json() or {}
    # profile fields (frontend should pass age/activity if available)
    try:
        profile = {
            "age": int(data.get("age") or 30),
            "gender": data.get("gender", "male"),
            "height_cm": float(data.get("height") or 0),
            "weight_kg": float(data.get("weight") or 0),
            "activity": data.get("activity", "moderate")
        }
    except Exception:
        profile = {"age": 30, "gender": "male", "height_cm": 0, "weight_kg": 0, "activity": "moderate"}

    totals = data.get("total_nutrients", {})    # expect same keys as /analyze output
    deficiencies = data.get("deficient", {})
    weather = data.get("weather", {})
    lang = data.get("lang", "en")

    # Make sure Gemini SDK & key exist
    if genai is None:
        return jsonify({"ok": False, "error": "Gemini SDK (google-genai) not installed on server. Run: pip install google-genai"}), 500
    if not GEMINI_API_KEY:
        return jsonify({"ok": False, "error": "GEMINI_API_KEY not set in environment (.env or system env)."}), 500

    try:
        consult_result = consult_with_gemini(profile, totals, deficiencies, weather, lang=lang)
        return jsonify({"ok": True, "consult": consult_result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

# ----------------- Gemini Chatbot -----------------

def _format_gemini_chat_prompt(message: str, analysis: Dict[str, Any], lang: str = "en") -> str:
    """
    Formats a prompt for the Gemini chat model, including nutrition context.
    """
    lines = []
    lines.append("You are a helpful and friendly AI Dietician Assistant.")
    lines.append("Your goal is to answer user questions about their nutrition, suggest meals, and provide advice based on their specific dietary analysis.")
    lines.append("Use the provided nutrition analysis as the primary context for your answers.")
    lines.append("\n--- NUTRITION ANALYSIS CONTEXT ---")
    if analysis and analysis.get("total_nutrients"):
        lines.append("\n[Total Nutrients]")
        for k, v in analysis["total_nutrients"].items():
            lines.append(f"- {k}: {v}")
    if analysis and analysis.get("deficient"):
        lines.append("\n[Deficient Nutrients]")
        for k, v in analysis["deficient"].items():
            lines.append(f"- {k}: need {v} more")
    lines.append("\n--- END CONTEXT ---")
    lines.append("\nNow, please answer the user's question concisely and helpfully.")
    if lang and lang != "en":
        lines.append(f"Respond in the following language: {lang}")
    lines.append(f"\nUser says: \"{message}\"")
    return "\n".join(lines)


def call_gemini_chat(message: str, analysis: Dict[str, Any], lang: str="en", model: str=DEFAULT_GEMINI_MODEL) -> str:
    """
    Calls the Gemini API with a formatted chat prompt.
    """
    client = _ensure_gemini_client()
    prompt = _format_gemini_chat_prompt(message, analysis, lang)
    resp = client.models.generate_content(model=model, contents=prompt)
    try:
        return resp.text if hasattr(resp, "text") else str(resp)
    except Exception:
        # Fallback for any response structure issues
        return str(resp)


@app.route("/chat", methods=["POST"])
def chat():
    """
    Chat endpoint for the AI Dietician Assistant.
    Accepts a user message and nutrition analysis data.
    """
    data = request.get_json() or {}
    message = data.get("message")
    analysis_data = data.get("analysis_data")
    lang = data.get("lang", "en")

    if not message:
        return jsonify({"ok": False, "error": "No message provided"}), 400

    # Make sure Gemini SDK & key exist
    if genai is None:
        return jsonify({"ok": False, "error": "Gemini SDK (google-genai) not installed on server. Run: pip install google-genai"}), 500
    if not GEMINI_API_KEY:
        return jsonify({"ok": False, "error": "GEMINI_API_KEY not set in environment (.env or system env)."}), 500

    try:
        chat_reply = call_gemini_chat(message, analysis_data, lang=lang)
        return jsonify({"ok": True, "reply": chat_reply})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

if __name__ == "__main__":
    # for dev only; in production use gunicorn
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
