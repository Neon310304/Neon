import re


def normalize_speech_text(value: str) -> str:
    return re.sub(r"\bBQL\b", "ban quản lí", value, flags=re.IGNORECASE)


def speech_text(value):
    value = re.sub(r"```[\s\S]*?```", "", value)
    value = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", value)
    value = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", value)
    value = re.sub(r"https?://\S+|<[^>]*>|\b\d{12}\b", "", value)
    value = re.sub(r"(?im)^\s*(?:#{1,6}|[-*>])\s*", "", value)
    return normalize_speech_text(re.sub(r"\s+", " ", re.sub(r"[*_`|]", "", value)).strip())


class PublicSpeechSegments:
    def __init__(self):
        self.pending = ""
        self.index = 0
        self.spoken = 0
        self.truncated = False

    def append(self, value, final=False):
        self.pending += value
        segments = []
        if "```" in self.pending:
            if not final:
                return segments
            self.pending = speech_text(self.pending)
        while self.pending.strip() and self.spoken < 5000:
            maximum = 120 if self.index == 0 else 700
            minimum = 1 if self.index == 0 else 350
            boundary = None
            for match in re.finditer(r"[.!?](?:\s+|$)|\n", self.pending):
                candidate = match.end()
                if minimum <= candidate <= maximum:
                    boundary = candidate
                    break
            if boundary is None:
                if len(self.pending) > maximum:
                    boundary = self.pending.rfind(" ", 0, maximum + 1)
                    if boundary <= 0:
                        boundary = maximum
                elif final:
                    boundary = len(self.pending)
                else:
                    break
            clean = speech_text(self.pending[:boundary])
            text = clean[:5000 - self.spoken]
            self.truncated = self.truncated or len(text) < len(clean)
            self.pending = self.pending[boundary:]
            if text:
                self.index += 1
                self.spoken += len(text)
                segments.append({"type": "speech_segment", "segment_id": f"speech-{self.index}", "index": self.index, "text": text})
        self.truncated = self.truncated or (self.spoken >= 5000 and bool(self.pending.strip()))
        return segments
