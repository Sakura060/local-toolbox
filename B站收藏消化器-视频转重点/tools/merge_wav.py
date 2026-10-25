"""Merge tts_out/topic*.wav with silence gaps into one test WAV (16k mono 16bit)."""
from pathlib import Path
import wave

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "tts_out" / "podcast_test.wav"
GAP_SECONDS = 1.5

parts = sorted((ROOT / "tts_out").glob("topic*.wav"))
assert parts, "no topic wavs found; run tts_gen.ps1 first"

with wave.open(str(parts[0]), "rb") as w0:
    params = w0.getparams()

silence = b"\x00" * int(params.framerate * GAP_SECONDS) * params.sampwidth * params.nchannels

with wave.open(str(OUT), "wb") as out:
    out.setparams(params)
    for i, p in enumerate(parts):
        with wave.open(str(p), "rb") as w:
            assert w.getframerate() == params.framerate, f"rate mismatch in {p.name}"
            out.writeframes(w.readframes(w.getnframes()))
        if i < len(parts) - 1:
            out.writeframes(silence)

with wave.open(str(OUT), "rb") as w:
    dur = w.getnframes() / w.getframerate()
print(f"OK {OUT} duration={dur:.1f}s rate={params.framerate}")
