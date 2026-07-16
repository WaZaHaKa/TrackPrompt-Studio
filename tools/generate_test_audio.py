from __future__ import annotations

import argparse
import wave
from pathlib import Path

import numpy as np
from numpy.typing import NDArray


SAMPLE_RATE = 22_050
FloatArray = NDArray[np.float64]


def _fade(signal: FloatArray, milliseconds: float = 8.0) -> FloatArray:
    count = min(signal.shape[0] // 2, int(SAMPLE_RATE * milliseconds / 1000))
    if count <= 0:
        return signal
    envelope = np.ones(signal.shape[0], dtype=np.float64)
    envelope[:count] = np.linspace(0, 1, count)
    envelope[-count:] = np.linspace(1, 0, count)
    if signal.ndim == 2:
        return signal * envelope[:, None]
    return signal * envelope


def _tone(frequency: float, duration: float, amplitude: float = 0.25, phase: float = 0.0) -> FloatArray:
    time = np.arange(int(SAMPLE_RATE * duration), dtype=np.float64) / SAMPLE_RATE
    return amplitude * np.sin(2 * np.pi * frequency * time + phase)


def _chord(frequencies: tuple[float, ...], duration: float, amplitude: float = 0.38) -> FloatArray:
    tones = sum((_tone(frequency, duration, amplitude / len(frequencies)) for frequency in frequencies), np.zeros(int(SAMPLE_RATE * duration)))
    return _fade(tones, 15)


def _click_track(bpm: float, duration: float, accent_every: int | None = None) -> FloatArray:
    samples = np.zeros(int(SAMPLE_RATE * duration), dtype=np.float64)
    interval = 60.0 / bpm
    click_length = int(SAMPLE_RATE * 0.035)
    time = np.arange(click_length, dtype=np.float64) / SAMPLE_RATE
    envelope = np.exp(-time * 75)
    for index, position in enumerate(np.arange(0, duration, interval)):
        start = int(position * SAMPLE_RATE)
        end = min(samples.size, start + click_length)
        if start >= samples.size:
            break
        accented = accent_every is not None and index % accent_every == 0
        frequency = 1500.0 if accented else 900.0
        amplitude = 0.95 if accented else 0.5
        click = amplitude * np.sin(2 * np.pi * frequency * time) * envelope
        samples[start:end] += click[: end - start]
    return np.clip(samples, -1, 1)


def _write_wav(path: Path, signal: FloatArray, sample_rate: int = SAMPLE_RATE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if signal.ndim == 1:
        channels = 1
        frames = signal[:, None]
    else:
        channels = signal.shape[1]
        frames = signal
    pcm = np.round(np.clip(frames, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(pcm.tobytes())


def _arrangement() -> FloatArray:
    intro = _fade(_tone(261.63, 4, 0.08), 40)
    section_a = _chord((261.63, 329.63, 392.00), 2, 0.38)
    section_a = np.concatenate((section_a, _chord((349.23, 440.00, 523.25), 2, 0.38)))
    section_b = _chord((392.00, 493.88, 587.33), 2, 0.65)
    section_b = np.concatenate((section_b, _chord((440.00, 554.37, 659.25), 2, 0.65)))
    outro = _fade(_tone(261.63, 4, 0.1), 800)
    return np.concatenate((intro, section_a, section_b, section_a.copy(), outro))


def _fade_envelope(duration: float, *, fade_in: bool) -> FloatArray:
    count = int(SAMPLE_RATE * duration)
    return np.linspace(0.0, 1.0, count) if fade_in else np.linspace(1.0, 0.0, count)


def _syncopated(bpm: float, duration: float) -> FloatArray:
    base = np.zeros(int(SAMPLE_RATE * duration), dtype=np.float64)
    beat = 60.0 / bpm
    click = _click_track(bpm * 2.0, duration) * 0.25
    for position in np.arange(beat * 0.75, duration, beat * 2.0):
        start = int(position * SAMPLE_RATE)
        length = min(int(SAMPLE_RATE * 0.05), base.size - start)
        if length > 0:
            base[start : start + length] += _fade(_tone(700, length / SAMPLE_RATE, 0.8))[:length]
    return np.clip(base + click, -1.0, 1.0)


def generate(output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    files: dict[str, FloatArray] = {
        "120bpm_click.wav": _click_track(120, 12),
        "133bpm_click.wav": _click_track(133, 16),
        "90bpm_halftime.wav": _click_track(90, 16, accent_every=2),
        "174bpm_dense_subdivisions.wav": np.clip(
            _click_track(174, 16) + 0.22 * _click_track(348, 16), -1.0, 1.0
        ),
        "90bpm_accented_4_4.wav": _click_track(90, 16, accent_every=4),
        "three_four.wav": _click_track(120, 12, accent_every=3),
        "six_eight.wav": _click_track(180, 12, accent_every=6),
        "syncopated.wav": _syncopated(120, 16),
        "sparse_kick.wav": _click_track(60, 16),
        "dense_hihat.wav": np.clip(
            0.7 * _click_track(120, 16) + 0.18 * _click_track(480, 16), -1.0, 1.0
        ),
        "tempo_change.wav": np.concatenate((_click_track(100, 8), _click_track(140, 8))),
        "c_major_progression.wav": np.concatenate(
            (
                _chord((261.63, 329.63, 392.00), 2),
                _chord((349.23, 440.00, 523.25), 2),
                _chord((392.00, 493.88, 587.33), 2),
                _chord((261.63, 329.63, 392.00), 2),
            )
        ),
        "a_minor_progression.wav": np.concatenate(
            (
                _chord((220.00, 261.63, 329.63), 2),
                _chord((293.66, 349.23, 440.00), 2),
                _chord((329.63, 392.00, 493.88), 2),
                _chord((220.00, 261.63, 329.63), 2),
            )
        ),
        "tone_sequence_known_range.wav": np.concatenate(tuple(_tone(frequency, 0.7, 0.3) for frequency in (220, 261.63, 329.63, 440, 659.25))),
        "mono.wav": _tone(440, 3, 0.25),
        "clipped.wav": np.clip(_tone(110, 3, 2.0), -1, 1),
        "quiet.wav": _tone(440, 3, 0.0002),
        "silence.wav": np.zeros(SAMPLE_RATE * 3, dtype=np.float64),
        "silence_then_music.wav": np.concatenate(
            (np.zeros(SAMPLE_RATE * 5), _tone(330, 6, 0.2))
        ),
        "quiet_intro_loud_music.wav": np.concatenate(
            (_tone(220, 4, 0.006), _tone(220, 8, 0.65) + 0.2 * _click_track(120, 8))
        ),
        "gradual_fade_in.wav": _tone(330, 8, 0.35) * _fade_envelope(8, fade_in=True),
        "fade_out.wav": _tone(330, 8, 0.35) * _fade_envelope(8, fade_in=False),
        "mastered_electronic.wav": np.clip(
            _tone(55, 12, 0.75) + _click_track(128, 12), -0.999, 0.999
        ),
        "sparse_ambient.wav": np.concatenate(
            tuple(
                np.concatenate((_fade(_tone(180 + index * 35, 1.5, 0.025), 300), np.zeros(SAMPLE_RATE)))
                for index in range(5)
            )
        ),
        "isolated_clicks_then_music.wav": np.concatenate(
            (
                _click_track(30, 4) * 0.8,
                np.zeros(SAMPLE_RATE * 2),
                _tone(260, 6, 0.2) + 0.2 * _click_track(120, 6),
            )
        ),
        "continuous_loop.wav": np.tile(_chord((220.0, 277.18, 329.63), 2, 0.25), 8),
        "build_drop.wav": np.concatenate(
            (
                _tone(110, 6, 0.08) * np.linspace(0.2, 1.0, SAMPLE_RATE * 6),
                np.clip(_tone(55, 6, 0.6) + _click_track(128, 6), -1.0, 1.0),
            )
        ),
        "arrangement_intro_a_b_a_outro.wav": _arrangement(),
    }
    left = _tone(330, 4, 0.35)
    right = _tone(550, 4, 0.35, phase=np.pi / 2)
    files["stereo_wide.wav"] = np.column_stack((left, right))
    files["stereo_one_channel.wav"] = np.column_stack((np.zeros_like(left), left))
    rng = np.random.default_rng(20260715)
    files["rubato_untrackable.wav"] = np.clip(
        _tone(240, 12, 0.08) + rng.normal(0.0, 0.015, SAMPLE_RATE * 12), -1.0, 1.0
    )
    files["atonal_noise.wav"] = rng.normal(0.0, 0.08, SAMPLE_RATE * 8)
    written: list[Path] = []
    for filename, samples in files.items():
        path = output_dir / filename
        _write_wav(path, samples)
        written.append(path)
    (output_dir / "invalid_media.bin").write_bytes(b"not audio\x00\xff\x10")
    (output_dir / "truncated.wav").write_bytes(b"RIFF\x10\x00\x00\x00WAVEfmt ")
    written.extend((output_dir / "invalid_media.bin", output_dir / "truncated.wav"))
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate deterministic synthetic TrackPrompt audio fixtures.")
    parser.add_argument("--output-dir", type=Path, default=Path("test-fixtures"))
    args = parser.parse_args()
    written = generate(args.output_dir)
    print(f"Generated {len(written)} synthetic fixtures in {args.output_dir.resolve()}")


if __name__ == "__main__":
    main()
