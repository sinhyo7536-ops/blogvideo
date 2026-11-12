"""내장 배경음악 생성기.

외부 음원 대신 코드로 직접 합성한 짧은 반복용 배경음악을 만든다(저작권 문제 없음, CC0).
실행: python packaging/make_music.py  → assets/music/*.mp3
"""
from __future__ import annotations

import math
import random
import struct
import subprocess
import sys
import wave
from pathlib import Path

SR = 44100
OUT = Path(__file__).resolve().parent.parent / "assets" / "music"

NOTE = {n: i for i, n in enumerate(["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"])}


def freq(name: str) -> float:
    pitch, octave = name[:-1], int(name[-1])
    return 440.0 * 2 ** ((NOTE[pitch] + 12 * (octave + 1) - 69) / 12)


def env(t: float, length: float, attack: float, release: float) -> float:
    if t < attack:
        return t / attack
    if t > length - release:
        return max(0.0, (length - t) / release)
    return 1.0


def render(chords, bpm, bars_per_chord, arp, seed, pad_gain=0.18, arp_gain=0.10, bass_gain=0.14):
    random.seed(seed)
    beat = 60 / bpm
    chord_len = beat * 4 * bars_per_chord
    total = chord_len * len(chords)
    n = int(total * SR)
    buf = [0.0] * n

    def add_tone(f, start, length, gain, attack, release, shape="sine"):
        s0 = int(start * SR)
        for i in range(int(length * SR)):
            idx = s0 + i
            if idx >= n:
                break
            t = i / SR
            ph = 2 * math.pi * f * t
            if shape == "tri":
                v = 2 / math.pi * math.asin(math.sin(ph))
            else:
                v = math.sin(ph) + 0.25 * math.sin(2 * ph)
            buf[idx] += v * gain * env(t, length, attack, release)

    for ci, chord in enumerate(chords):
        start = ci * chord_len
        for note in chord:
            add_tone(freq(note), start, chord_len, pad_gain / len(chord), 0.8, 1.0)
        add_tone(freq(chord[0][:-1] + "2"), start, chord_len, bass_gain, 0.05, 0.6, "tri")
        if arp:
            steps = int(chord_len / (beat / 2))
            tones = [c[:-1] + str(int(c[-1]) + 1) for c in chord]
            for s in range(steps):
                f = freq(tones[(s * 2 + (s // 4)) % len(tones)])
                add_tone(f, start + s * beat / 2, beat * 0.9, arp_gain, 0.01, beat * 0.7, "tri")

    peak = max(abs(x) for x in buf) or 1.0
    return [x / peak * 0.8 for x in buf]


def write(name: str, samples: list[float]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    wav = OUT / f"{name}.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(b"".join(struct.pack("<h", int(x * 32767)) for x in samples))
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav), "-ac", "2",
                    "-c:a", "libmp3lame", "-b:a", "128k", str(OUT / f"{name}.mp3")], check=True)
    wav.unlink()


TRACKS = {
    "잔잔한_피아노풍": dict(chords=[["C4", "E4", "G4"], ["A3", "C4", "E4"], ["F3", "A3", "C4"], ["G3", "B3", "D4"]] * 2,
                       bpm=72, bars_per_chord=1, arp=True, seed=1),
    "밝은_산책": dict(chords=[["D4", "F#4", "A4"], ["B3", "D4", "F#4"], ["G3", "B3", "D4"], ["A3", "C#4", "E4"]] * 2,
                  bpm=100, bars_per_chord=1, arp=True, seed=2, arp_gain=0.12),
    "포근한_공간": dict(chords=[["F3", "A3", "C4", "E4"], ["D3", "F3", "A3", "C4"], ["A#3", "D4", "F4", "A4"], ["C4", "E4", "G4", "B4"]],
                   bpm=60, bars_per_chord=2, arp=False, seed=3, pad_gain=0.25),
}

if __name__ == "__main__":
    only = set(sys.argv[1:])
    for name, spec in TRACKS.items():
        if only and name not in only:
            continue
        write(name, render(**spec))
        print("wrote", name)
