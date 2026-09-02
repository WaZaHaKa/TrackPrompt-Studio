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


def _decaying_hit(
    frequency: float,
    duration: float,
    amplitude: float,
    decay: float,
    *,
    noise: float = 0.0,
    seed: int = 0,
) -> FloatArray:
    time = np.arange(int(SAMPLE_RATE * duration), dtype=np.float64) / SAMPLE_RATE
    signal = amplitude * np.sin(2 * np.pi * frequency * time) * np.exp(-time * decay)
    if noise > 0.0:
        rng = np.random.default_rng(seed)
        signal += noise * rng.normal(0.0, 1.0, time.size) * np.exp(-time * decay * 1.4)
    return signal


def _place_hits(
    duration: float,
    bpm: float,
    beat_positions: tuple[float, ...],
    hit: FloatArray,
    *,
    bars: int | None = None,
) -> FloatArray:
    signal = np.zeros(int(SAMPLE_RATE * duration), dtype=np.float64)
    beat_seconds = 60.0 / bpm
    bar_seconds = beat_seconds * 4.0
    bar_count = bars if bars is not None else int(np.ceil(duration / bar_seconds))
    for bar in range(bar_count):
        for beat_position in beat_positions:
            start = int((bar * bar_seconds + beat_position * beat_seconds) * SAMPLE_RATE)
            if start >= signal.size:
                continue
            end = min(signal.size, start + hit.size)
            signal[start:end] += hit[: end - start]
    return signal


def _four_on_floor(bpm: float, duration: float, *, sparse: bool = False) -> FloatArray:
    kick = _decaying_hit(54.0, 0.22, 0.72, 18.0)
    hat = _decaying_hit(3600.0, 0.05, 0.14, 65.0, noise=0.08, seed=11)
    kicks = _place_hits(duration, bpm, (0.0, 1.0, 2.0, 3.0), kick)
    hat_positions = (0.5, 1.5, 2.5, 3.5) if sparse else (
        0.5,
        1.0,
        1.5,
        2.0,
        2.5,
        3.0,
        3.5,
    )
    hats = _place_hits(duration, bpm, hat_positions, hat)
    return np.clip(kicks + hats, -1.0, 1.0)


def _pulse_bass(bpm: float, duration: float, frequency: float = 55.0) -> FloatArray:
    beat_seconds = 60.0 / bpm
    note = _fade(_tone(frequency, beat_seconds * 0.72, 0.24), 10)
    return _place_hits(duration, bpm, (0.0, 1.0, 2.0, 3.0), note)


def _delayed_chords(bpm: float, duration: float) -> FloatArray:
    beat_seconds = 60.0 / bpm
    chord = _chord((146.83, 220.0, 293.66), beat_seconds * 0.42, 0.2)
    dry = _place_hits(duration, bpm, (0.75, 2.75), chord)
    delay = int(SAMPLE_RATE * beat_seconds * 0.75)
    echoed = np.zeros_like(dry)
    if delay < dry.size:
        echoed[delay:] += dry[:-delay] * 0.48
    if delay * 2 < dry.size:
        echoed[delay * 2 :] += dry[: -delay * 2] * 0.24
    return dry + echoed


def _synthetic_vocal_timbre(duration: float) -> FloatArray:
    time = np.arange(int(SAMPLE_RATE * duration), dtype=np.float64) / SAMPLE_RATE
    phrase = (
        0.12 * np.sin(2 * np.pi * 180.0 * time)
        + 0.06 * np.sin(2 * np.pi * 540.0 * time)
        + 0.035 * np.sin(2 * np.pi * 900.0 * time)
    )
    envelope = np.maximum(0.0, np.sin(2 * np.pi * 1.5 * time)) ** 1.5
    return phrase * envelope


def _spoken_rhythmic_vocal(duration: float, bpm: float, *, seed: int = 91) -> FloatArray:
    """Return deterministic nonverbal formant/noise bursts resembling rhythmic speech."""

    samples = np.zeros(int(SAMPLE_RATE * duration), dtype=np.float64)
    rng = np.random.default_rng(seed)
    beat = 60.0 / bpm
    for index, position in enumerate(np.arange(beat * 0.25, duration, beat * 0.5)):
        length = min(int(SAMPLE_RATE * beat * (0.22 if index % 3 else 0.34)), samples.size)
        if length <= 0:
            continue
        time = np.arange(length, dtype=np.float64) / SAMPLE_RATE
        envelope = np.sin(np.linspace(0.0, np.pi, length)) ** 1.8
        formants = (
            0.08 * np.sin(2 * np.pi * (170.0 + 15.0 * (index % 4)) * time)
            + 0.045 * np.sin(2 * np.pi * 720.0 * time)
            + 0.025 * rng.normal(0.0, 1.0, length)
        ) * envelope
        start = int(position * SAMPLE_RATE)
        end = min(samples.size, start + length)
        if start < samples.size:
            samples[start:end] += formants[: end - start]
    return np.clip(samples, -1.0, 1.0)


def _melodic_vocal(duration: float, bpm: float) -> FloatArray:
    """Return a deterministic nonverbal pitched-vocal proxy with repeated hooks."""

    beat = 60.0 / bpm
    notes = (220.0, 277.18, 329.63, 277.18)
    parts: list[FloatArray] = []
    remaining = duration
    index = 0
    while remaining > 0:
        note_duration = min(beat * 1.5, remaining)
        time = np.arange(int(SAMPLE_RATE * note_duration), dtype=np.float64) / SAMPLE_RATE
        frequency = notes[index % len(notes)]
        vibrato = np.sin(2 * np.pi * 5.2 * time) * 2.0
        phase = 2 * np.pi * np.cumsum(frequency + vibrato) / SAMPLE_RATE
        phrase = (0.13 * np.sin(phase) + 0.045 * np.sin(2 * phase))
        parts.append(_fade(phrase, 35))
        remaining -= note_duration
        index += 1
    result = np.concatenate(parts)
    target = int(SAMPLE_RATE * duration)
    return np.pad(result, (0, max(0, target - result.size)))[:target]


def genre_regression_signals(duration: float = 16.0) -> dict[str, FloatArray]:
    """Return deterministic, nonverbal genre-proxy signals for MIR regressions."""

    techno = np.clip(
        _four_on_floor(132.0, duration)
        + _pulse_bass(132.0, duration, 55.0)
        + 0.035 * _tone(220.0, duration),
        -1.0,
        1.0,
    )
    minimal = np.clip(
        _four_on_floor(126.0, duration, sparse=True)
        + 0.6 * _pulse_bass(126.0, duration, 49.0),
        -1.0,
        1.0,
    )
    dub_techno = np.clip(
        0.72 * _four_on_floor(120.0, duration, sparse=True)
        + _pulse_bass(120.0, duration, 49.0)
        + _delayed_chords(120.0, duration),
        -1.0,
        1.0,
    )
    layers = np.linspace(0.2, 1.0, int(SAMPLE_RATE * duration))
    progressive = np.clip(
        _four_on_floor(126.0, duration)
        + _pulse_bass(126.0, duration, 55.0)
        + layers * (
            0.08 * _tone(220.0, duration)
            + 0.05 * _tone(329.63, duration)
        ),
        -1.0,
        1.0,
    )
    break_kick = _decaying_hit(62.0, 0.2, 0.72, 20.0)
    break_snare = _decaying_hit(190.0, 0.12, 0.36, 28.0, noise=0.2, seed=22)
    breakbeat = np.clip(
        _place_hits(duration, 124.0, (0.0, 1.75, 2.5), break_kick)
        + _place_hits(duration, 124.0, (1.0, 3.0, 3.5), break_snare)
        + 0.8 * _pulse_bass(124.0, duration, 55.0),
        -1.0,
        1.0,
    )
    hiphop = np.clip(
        _place_hits(duration, 90.0, (0.0, 2.5), break_kick)
        + _place_hits(duration, 90.0, (1.0, 3.0), break_snare)
        + 0.8 * _pulse_bass(90.0, duration, 49.0),
        -1.0,
        1.0,
    )
    r_and_b = np.clip(
        0.58 * hiphop
        + _synthetic_vocal_timbre(duration)
        + 0.08 * _tone(261.63, duration)
        + 0.05 * _tone(329.63, duration),
        -1.0,
        1.0,
    )
    saw = sum(
        _tone(110.0 * harmonic, duration, 0.12 / harmonic)
        for harmonic in range(1, 7)
    )
    rock = np.clip(
        saw
        + _place_hits(duration, 118.0, (0.0, 2.0), break_kick)
        + _place_hits(duration, 118.0, (1.0, 3.0), break_snare),
        -1.0,
        1.0,
    )
    ambient = np.clip(
        _fade(_tone(110.0, duration, 0.1), 900)
        + _fade(_tone(164.81, duration, 0.08), 1200)
        + _fade(_tone(246.94, duration, 0.06), 1500),
        -1.0,
        1.0,
    )
    return {
        "genre_techno_four_floor.wav": techno,
        "genre_minimal_techno.wav": minimal,
        "genre_dub_techno.wav": dub_techno,
        "genre_progressive_house.wav": progressive,
        "genre_breakbeat.wav": breakbeat,
        "genre_hip_hop.wav": hiphop,
        "genre_r_and_b.wav": r_and_b,
        "genre_rock.wav": rock,
        "genre_ambient_electronic.wav": ambient,
    }


def hybrid_genre_regression_signals(duration: float = 16.0) -> dict[str, FloatArray]:
    """Return six deterministic, nonverbal hybrid-track regression fixtures."""

    genres = genre_regression_signals(duration)
    techno = genres["genre_techno_four_floor.wav"]
    progressive = genres["genre_progressive_house.wav"]
    hiphop = genres["genre_hip_hop.wav"]
    spoken_132 = _spoken_rhythmic_vocal(duration, 132.0, seed=132)
    spoken_90 = _spoken_rhythmic_vocal(duration, 90.0, seed=90)
    pop_vocal = _melodic_vocal(duration, 126.0)
    third = int(techno.size / 3)
    vocal_outro = techno.copy()
    vocal_outro[-third:] = 0.18 * techno[-third:] + 2.4 * spoken_132[-third:]
    genre_change = np.concatenate(
        (
            techno[:third],
            progressive[third : third * 2],
            hiphop[third * 2 :],
        )
    )
    return {
        "hybrid_techno_instrumental.wav": techno,
        "hybrid_techno_spoken_rhythmic_vocals.wav": np.clip(techno + 1.8 * spoken_132, -1.0, 1.0),
        "hybrid_progressive_house_pop_vocals.wav": np.clip(progressive + 1.5 * pop_vocal, -1.0, 1.0),
        "hybrid_hip_hop_rap_vocals.wav": np.clip(hiphop + 1.8 * spoken_90, -1.0, 1.0),
        "hybrid_electronic_vocal_only_outro.wav": np.clip(vocal_outro, -1.0, 1.0),
        "hybrid_section_genre_change.wav": np.clip(genre_change, -1.0, 1.0),
    }


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
    files.update(genre_regression_signals())
    files.update(hybrid_genre_regression_signals())
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
