"""Pre-download faster-whisper model so later runs skip network."""
import sys
import time

from faster_whisper import WhisperModel

size = sys.argv[1] if len(sys.argv) > 1 else "base"
t0 = time.time()
print(f"loading {size} (downloads on first use)...", flush=True)
model = WhisperModel(size, device="cpu", compute_type="int8")
print(f"model ready in {time.time()-t0:.1f}s", flush=True)
