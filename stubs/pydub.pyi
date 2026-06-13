"""Type stubs override for `pydub`.

The `pydub` package ships without type stubs, so pyright shows
"Stub file not found for 'pydub'" warnings.

This stub declares only the surface we use: `AudioSegment.from_file()`,
`AudioSegment.export()`, `AudioSegment.apply_gain()`, and the
`pydub.effects.normalize` helper.
"""

from typing import Any

class AudioSegment:
    @staticmethod
    def from_file(
        file: Any,
        format: str | None = None,
        codec: str | None = None,
        frame_rate: int | None = None,
        channels: int | None = None,
        sample_width: int | None = None,
    ) -> "AudioSegment": ...
    @staticmethod
    def silent(duration: int = 0, frame_rate: int = 11025) -> "AudioSegment": ...
    def apply_gain(self, gain_in_db: float) -> "AudioSegment": ...
    def append(
        self,
        seg: "AudioSegment",
        crossfade: int = 0,
    ) -> "AudioSegment": ...
    def export(
        self,
        out_f: Any = None,
        format: str | None = None,
        codec: str | None = None,
        bitrate: str | None = None,
        parameters: list[str] | None = None,
    ) -> Any: ...
    def __add__(self, other: "AudioSegment") -> "AudioSegment": ...
    def __iadd__(self, other: "AudioSegment") -> "AudioSegment": ...
    @property
    def frame_rate(self) -> int: ...
    @property
    def channels(self) -> int: ...
    @property
    def sample_width(self) -> int: ...
    @property
    def dBFS(self) -> float: ...
    @property
    def max_dBFS(self) -> float: ...
    @property
    def rms(self) -> float: ...
    @property
    def duration_seconds(self) -> float: ...
    @property
    def raw_data(self) -> bytes: ...

class effects:
    @staticmethod
    def normalize(segment: AudioSegment, headroom: float = 0.1) -> AudioSegment: ...
