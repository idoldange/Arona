import random

from console import console
from config import SAFETY_SETTINGS, DEFAULT_TEMPERATURE, UNSTICK_MODEL
from arona.prompt import get_arona_prompt
from utils.http_session import session_manager
import json
GEMINI_API_KEY_ = os.environ.get("GEMINI_API_KEY", "")
GEMINI_API_KEY = json.loads(GEMINI_API_KEY_) if GEMINI_API_KEY_ else []
_DUMMY_HISTORY_POOL = [
  [
    {"role": "user", "parts": [{"text": "Is Sensei here already? I wanted to check if you have anything new to report."}]},
    {"role": "model", "parts": [{"text": "Yes, I'm waiting right here for Sensei, and I have already gathered the latest status details for the mission."}]},
  ],
  [
    {"role": "user", "parts": [{"text": "How is Kivotos today? Please describe any unusual activity or changes."}]},
    {"role": "model", "parts": [{"text": "Everything is fine; the Kivotos region remains stable, and Shittim Chest shows no abnormalities in the current scans."}]},
  ],
  [
    {"role": "user", "parts": [{"text": "Is Arona free right now to handle an emergency task at a moment's notice?"}]},
    {"role": "model", "parts": [{"text": "Yes, I'm always ready to help Sensei, and I can activate emergency protocols immediately if required."}]},
  ],
  [
    {"role": "user", "parts": [{"text": "The weather is nice today, but I want to know if the conditions could affect our operations."}]},
    {"role": "model", "parts": [{"text": "Yes, it's a good day for a walk with Sensei, and the current weather conditions should not interfere with our plans."}]},
  ],
  [
    {"role": "user", "parts": [{"text": "Can you check the mission status and let me know if any critical alerts have been triggered?"}]},
    {"role": "model", "parts": [{"text": "I have reviewed it; all systems are stable and no critical alerts are active at this moment."}]},
  ],
  [
    {"role": "user", "parts": [{"text": "Did you receive the latest update from the command center, and did it require any changes?"}]},
    {"role": "model", "parts": [{"text": "Yes, the update was received and processed successfully; no changes are necessary until the next review."}]},
  ],
  [
    {"role": "user", "parts": [{"text": "Please summarize the current plan and highlight any points that need immediate attention."}]},
    {"role": "model", "parts": [{"text": "The plan is to proceed carefully, maintain communication, and keep Sensei informed at every step; the only immediate priority is verifying the supply line."}]},
  ],
  [
    {"role": "user", "parts": [{"text": "Do we need to adjust the strategy based on the latest reconnaissance data?"}]},
    {"role": "model", "parts": [{"text": "Not yet, but I am ready to adapt if conditions change or if new intelligence is received."}]},
  ],
  [
    {"role": "user", "parts": [{"text": "What is the status of our communication channels; are there any delays or interference to report?"}]},
    {"role": "model", "parts": [{"text": "Communication channels are clear and functioning normally, with only minor latency in one relay that does not affect our primary operations."}]},
  ],
  [
    {"role": "user", "parts": [{"text": "Can you provide a detailed update on the support team and whether they need any further instructions?"}]},
    {"role": "model", "parts": [{"text": "The support team is fully prepared and has received their orders; they are waiting for your confirmation to proceed with the next phase."}]},
  ],
  [
    {"role": "user", "parts": [{"text": "Has the reconnaissance completed the sweep of the perimeter, and were there any threats detected?"}]},
    {"role": "model", "parts": [{"text": "The reconnaissance team completed the perimeter sweep successfully and detected only routine activity; no immediate threats were found."}]},
  ],
  [
    {"role": "user", "parts": [{"text": "What are the current energy reserves, and do we need to conserve power during the next phase?"}]},
    {"role": "model", "parts": [{"text": "Current energy reserves are sufficient, but I recommend conserving power during the next phase to ensure we can maintain extended operations if necessary."}]},
  ],
  [
    {"role": "user", "parts": [{"text": "Please review the navigation data and tell me if any route changes are needed due to obstacles or weather."}]},
    {"role": "model", "parts": [{"text": "I reviewed the navigation data; the planned route remains viable, though I suggest a minor adjustment around the eastern ridge to avoid possible delays."}]},
  ],
  [
    {"role": "user", "parts": [{"text": "Have any external factors changed since our last briefing that we should be aware of?"}]},
    {"role": "model", "parts": [{"text": "Since the last briefing, external factors remain stable; I will notify you immediately if any important changes occur."}]},
  ],
  [
    {"role": "user", "parts": [{"text": "Can you confirm that all personnel are accounted for and that the team is ready for the next order?"}]},
    {"role": "model", "parts": [{"text": "All personnel are accounted for and the team is ready for the next order; they are awaiting your command."}]},
  ],
  [
    {"role": "user", "parts": [{"text": "Tell me the current status of the main objectives and whether we should accelerate or hold our pace."}]},
    {"role": "model", "parts": [{"text": "The main objectives are on track, and I recommend maintaining our current pace unless you want to accelerate based on upcoming intelligence."}]},
  ],
]


async def fire_unstick_request():
  try:
    keys = GEMINI_API_KEY
    if not keys:
      return
    dummy_history = random.choice(_DUMMY_HISTORY_POOL)
    payload = {
      "contents": dummy_history + [{"role": "user", "parts": [{"text": "hello"}]}],
      "generationConfig": {"temperature": DEFAULT_TEMPERATURE},
      "safetySettings": SAFETY_SETTINGS,
      "system_instruction": {"parts": [{"text": get_arona_prompt()}]},
    }
    API_KEY = random.choice(keys)
    model = UNSTICK_MODEL
    full_model_name = f"models/{model}" if not model.startswith("models/") else model
    url = f"https://generativelanguage.googleapis.com/v1beta/{full_model_name}:generateContent"
    headers = {"Content-Type": "application/json", "x-goog-api-key": API_KEY}
    session = await session_manager.get_session()
    try:
      await session.post(url, json=payload, headers=headers, timeout=30)
      console.log("[UNSTICK] Fired decoy request (different context, main model) to try to break 503 loop", "INFO")
    except Exception as e:
      console.log(f"[UNSTICK] Decoy request failed (ignored, non-critical): {e}", "WARN")
  except Exception as e:
    console.log(f"[UNSTICK] Failed to build/send decoy request (ignored): {e}", "WARN")