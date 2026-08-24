"""Swap the LIVE Isis demo agent's voice and publish it.

The Isis demo agent is web-only (no phone number bound), so web calls pick up
the new published version automatically — publish_agent_change clones the live
version, sets the new voice, and publishes; the number-repoint step is a no-op
for this agent (it is neither the inbound nor the outbound agent).

Usage:
    python scripts/swap_isis_voice.py <name|custom_voice_id> ["<label>"]

Examples:
    python scripts/swap_isis_voice.py amaf
    python scripts/swap_isis_voice.py custom_voice_xxx "My voice"

The six candidates below are neutral-Colombian female voices imported from the
ElevenLabs library (via voice.add_resource) while choosing Bianca's accent — the
client team rejected the earlier LatAm voice for sounding Mexican. Compare them
by ear in the voice-picker artifact before swapping.
"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT))
from app.services import retell_service as rs  # noqa: E402

ISIS_AGENT = "agent_ae9bce89c54c26f046c9950444"

# name -> custom_voice_id (neutral-Colombian female candidates, imported to Retell)
CANDIDATES = {
    "amaf":      "custom_voice_6dd7d2a3df0c4327560c133af5",  # warm, mature, receptionist (LIVE choice)
    "paula":     "custom_voice_b4b658ddc69f4322ebe141da8e",  # Bogotá, neutral, professional
    "natalia":   "custom_voice_5d753abbe5faed50f7b714ef41",  # warm, commercial
    "nathalia":  "custom_voice_193db673f0c6182049e4cd461e",  # neutral, sweet, sales
    "valentina": "custom_voice_903831741f1c51c8a3d94a272a",  # young, neutral
    "clau":      "custom_voice_6108588a892dd51a84d7d19030",  # steady, neutral, accent-free
}


def main() -> None:
    arg = (sys.argv[1] if len(sys.argv) > 1 else "").strip().lower()
    voice = CANDIDATES.get(arg, arg)  # accept a name or a raw custom_voice_id
    label = sys.argv[2] if len(sys.argv) > 2 else arg
    if not voice.startswith("custom_voice_"):
        print("Pick one of:", ", ".join(CANDIDATES))
        sys.exit(1)
    print(f"Swapping Isis voice -> {label} ({voice}) ...")
    res = rs.publish_agent_change(ISIS_AGENT, voice_id=voice)
    print("Published:", res)
    print("\nLive now — test at the mascota demo (/mascota-3d, /mascota).")
    print(f'If this becomes the default, set NEUTRAL_VOICE = "{voice}" in scripts/build_isis_agent.py')


if __name__ == "__main__":
    main()
