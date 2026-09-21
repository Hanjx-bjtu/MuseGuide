"""解析器包：把用户手写的音乐文本转为契约结构。

* :mod:`~app.services.parser.chords` —— 和弦进行（§3.4.1）
* :mod:`~app.services.parser.melody` —— 旋律音名序列（§3.4.2）
"""

from app.services.parser.chords import ChordParseError, parse_chord, parse_progression
from app.services.parser.melody import MelodyParseError, parse_melody, parse_notes

__all__ = [
    "ChordParseError",
    "MelodyParseError",
    "parse_chord",
    "parse_melody",
    "parse_notes",
    "parse_progression",
]
