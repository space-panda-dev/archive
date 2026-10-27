"""Audio-only vocal contours and conservative note-event candidates.

This is numerical mechanism for an extractor's own environment (ADR 0074),
not an archive writer or a judgment that the vocal stem carries the tune.
Measured motion stays in the contour; note events are an explicit, lossy
reading of its landings. No chart, lyrics, key, meter or model runtime enters
this module. Dependencies are loaded only when a numerical function runs.
"""
from __future__ import annotations

from typing import Any, Iterable

SAMPLE_RATE = SR = 16000
HOP_SECONDS = H = 0.01
RULE = "vocal-contour/1"
SETTINGS = {
    "confidence_on": 0.6, "confidence_keep": 0.35,
    "level_on_relative_db": -38, "level_keep_relative_db": -44,
    "trend_median_s": 0.19, "landing_velocity_window_s": 0.10,
    "landing_max_movement_semitones": 0.2, "landing_persist_s": 0.10,
    "target_change_semitones": 0.75, "movement_departure_semitones": 0.45,
    "lowband_hz": [80, 1800], "attack_lowband_rise_db": 3,
    "attack_rise_window_s": 0.03, "rmvpe_threshold": 0.03,
    "attack_pitch_change_max_semitones": 0.75,
    "attack_voiced_side_frames": 3, "attack_pitch_side_frames": 5,
    "render_fade_samples": 80, "render_amplitude": 0.14,
    "render_second_harmonic": 0.2, "reattack_side_seconds": 0.012,
}
NOTE_SETTINGS = {"rule": "vocal-contour-notes/1", "minimum_stable_frames": 3,
                 "maximum_step_semitones": 0.03, "maximum_span_semitones": 0.35}


def _libraries() -> None:
    global np, median_filter, butter, sosfiltfilt, find_peaks
    import numpy as np
    from scipy.ndimage import median_filter
    from scipy.signal import butter, sosfiltfilt, find_peaks


def _array(values: Any, name: str) -> Any:
    _libraries()
    out = np.asarray(values)
    if out.dtype.kind not in "fiu":
        raise ValueError(f"{name} must be numeric")
    if out.ndim != 1 or not np.isfinite(out).all():
        raise ValueError(f"{name} must be a finite one-dimensional array")
    return out


def _spans(mask: Any) -> list[tuple[int, int]]:
    edges = np.diff(np.r_[False, mask, False].astype(int))
    return list(zip(np.where(edges == 1)[0], np.where(edges == -1)[0]))

def _level(y: Any) -> Any:
    n = int(np.ceil(len(y) / 160))
    z = np.pad(y, (0, n * 160 - len(y))).reshape(n, 160)
    return 20 * np.log10(np.maximum(np.sqrt(np.mean(z * z, axis=1)), 1e-06))

def _segment(
    f0: Any, conf: Any, db: Any, y: Any,
    confidence_on: float = 0.6, confidence_keep: float = 0.35,
) -> dict[str, Any]:
    """Numerical gesture mechanism; input validation belongs to segment."""
    n = min(len(f0), len(conf), len(db))
    f0 = np.asarray(f0[:n])
    conf = np.asarray(conf[:n])
    db = np.asarray(db[:n])
    ref = (
        float(np.percentile(db[conf >= 0.8], 90))
        if np.sum(conf >= 0.8) > 50
        else float(np.percentile(db, 95))
    )
    voiced = np.zeros(n, dtype=bool)
    state = False
    for i in range(n):
        state = bool(
            f0[i] > 0
            and conf[i] >= (confidence_keep if state else confidence_on)
            and db[i] >= ref + (-44 if state else -38)
        )
        voiced[i] = state
    valid = (f0 > 0) & (conf >= confidence_keep)
    raw = np.where(f0 > 0, 69 + 12 * np.log2(np.maximum(f0, 1e-08) / 440), np.nan)
    filled = (
        np.interp(np.arange(n), np.where(valid)[0], raw[valid])
        if np.any(valid) else np.full(n, 60.0)
    )
    fast = median_filter(filled, size=3, mode='nearest')
    trend = median_filter(filled, size=19, mode='nearest')
    movement = abs(np.roll(trend, -5) - np.roll(trend, 5))
    stable = (movement <= 0.2) & voiced
    stable[:5] = False
    stable[-5:] = False
    events = []
    regions = _spans(voiced)
    render = fast.copy()
    unlanded = 0
    for ri, (a, b) in enumerate(regions):
        anchors = []
        hold = 0
        last = None
        for t in range(a, b):
            hold = hold + 1 if stable[t] else 0
            if hold < 10:
                continue
            target = float(np.median(trend[t - 9:t + 1]))
            first = t - 9
            if last is None or abs(target - last) >= 0.75:
                if last is not None:
                    recent = trend[max(a, first - 10):t + 1]
                    if np.mean(np.abs(recent - last) >= 0.75) < 0.8:
                        continue
                anchors.append((first, target))
                last = target
        if not anchors:
            events.append(dict(
                kind='unlanded_contour',
                onset=a * H,
                end=b * H,
                landing=None,
                target_midi=None,
                ambiguity=['No stable 100 ms landing; continuous contour retained, not deleted or forced into notes.'],
            ))
            unlanded += b - a
            continue
        for k, (landing, target) in enumerate(anchors):
            if k == 0:
                onset = a
                kind = 'voiced_attack_to_landing'
                old = None
            else:
                old = anchors[k - 1][1]
                depart = [i for i in range(anchors[k - 1][0] + 1, landing + 1) if abs(fast[i] - old) > 0.45]
                onset = depart[0] if depart else landing
                kind = 'legato_target_transition'
            next_landing = anchors[k + 1][0] if k + 1 < len(anchors) else b
            if k + 1 < len(anchors):
                depart = [i for i in range(landing + 1, next_landing + 1) if abs(fast[i] - target) > 0.45]
                end = depart[0] if depart else next_landing
            else:
                end = b
            render[max(landing, onset):max(landing, end)] = target
            events.append(dict(
                kind=kind,
                onset=onset * H,
                landing=landing * H,
                end=end * H,
                target_midi=target,
                from_target_midi=old,
                region=ri,
                ambiguity=(
                    ['Landing uses centered 190 ms context and 100 ms persistence; '
                     'not an annotated note onset.']
                    if kind == 'legato_target_transition'
                    else ['Initial motion remains onset-to-landing contour; '
                          'not a chromatic note ladder.']
                ),
            ))
        if ri and a - regions[ri - 1][1] < 10:
            events.append(dict(
                kind='short_voicing_gap_alternative',
                onset=a * H,
                end=a * H,
                gap_s=(a - regions[ri - 1][1]) * H,
                ambiguity=['Possible syllable reattack or tracker dropout; gap preserved without automatic bridge.'],
            ))
    low = sosfiltfilt(butter(3, [80, 1800], btype='bandpass', fs=SR, output='sos'), y)
    high = sosfiltfilt(butter(3, [2200, 7500], btype='bandpass', fs=SR, output='sos'), y)
    ld = median_filter(_level(low)[:n], size=3)
    hd = median_filter(_level(high)[:n], size=3)
    delay = min(3, n)
    lr = ld - np.r_[np.repeat(ld[0], delay), ld[:-delay]]
    hr = hd - np.r_[np.repeat(hd[0], delay), hd[:-delay]]
    pk, _ = find_peaks(lr, height=3, distance=8)
    cues = [int(t) for t in pk if voiced[t] and ld[t] > ref - 44]
    interval = np.diff(cues) * H
    periodic = len(cues) >= 4 and len(interval) > 0 and (float(np.std(interval) / max(np.mean(interval), 1e-06)) < 0.2)
    for t in cues:
        near = any(
            abs(t * H - event['onset']) < 0.08
            for event in events
            if event['kind'] in ['voiced_attack_to_landing', 'legato_target_transition']
        )
        if not near:
            events.append(dict(
                kind='possible_same_pitch_attack',
                onset=t * H,
                end=t * H,
                landing=None,
                target_midi=float(trend[t]),
                lowband_rise_db=float(lr[t]),
                highband_rise_db=float(hr[t]),
                ambiguity=(
                    ['Periodic amplitude modulation or regularly repeated articulation; '
                     'not automatically split.']
                    if periodic
                    else ['Low-band attack evidence may be breath/dynamics/bleed; '
                          'no automatic new pitched event.']
                ),
            ))
    return dict(
        events=sorted(events, key=lambda x: x['onset']),
        voiced=voiced,
        raw_midi=raw,
        trend_midi=trend,
        render_midi=render,
        confidence=conf,
        level_db=db,
        lowband_rise_db=lr,
        highband_rise_db=hr,
        stable=stable,
        unlanded_s=unlanded * H,
        periodic_attack_pattern=bool(periodic),
        reference_level_db=ref,
    )


def segment(f0: Any, confidence: Any, level_db: Any, audio: Any) -> dict[str, Any]:
    """Frozen gesture reading of aligned 10 ms rows and their 16 kHz audio.

    No octave correction or grid quantization is performed. The caller owns
    model inference, context, and placement in the recording's timeline.
    """
    f0 = _array(f0, "f0")
    confidence = _array(confidence, "confidence")
    level_db = _array(level_db, "level_db")
    audio = _array(audio, "audio")
    if not len(f0) == len(confidence) == len(level_db):
        raise ValueError("pitch, confidence and level must share one grid")
    if (f0 < 0).any() or ((confidence < 0) | (confidence > 1)).any():
        raise ValueError("f0 must be nonnegative and confidence must be in [0, 1]")
    if len(audio) < len(f0) * 160:
        raise ValueError("audio must cover every 10 ms frame")
    if not len(f0):
        return dict(events=[], voiced=np.zeros(0, dtype=bool), raw_midi=np.zeros(0),
                    trend_midi=np.zeros(0), render_midi=np.zeros(0), confidence=confidence,
                    level_db=level_db, lowband_rise_db=np.zeros(0), highband_rise_db=np.zeros(0),
                    stable=np.zeros(0, dtype=bool), unlanded_s=0.0,
                    periodic_attack_pattern=False, reference_level_db=None)
    return _segment(f0, confidence, level_db, audio)


def replace_moving(gesture: dict[str, Any], rmvpe_f0: Any) -> dict[str, Any]:
    """Replace only voiced, non-held rows where the second tracker speaks.

    Held targets and unvoiced rows remain bit-identical. This is an explicit
    hybrid reading, not a judgment about either tracker's correctness.
    """
    old = _array(gesture["render_midi"], "render_midi")
    f0 = _array(rmvpe_f0, "rmvpe_f0")
    voice = np.asarray(gesture["voiced"], dtype=bool)
    if f0.shape != old.shape or voice.shape != old.shape or (f0 < 0).any():
        raise ValueError("replacement pitch and voicing must share the contour grid")
    held = np.zeros(len(old), dtype=bool)
    for event in gesture["events"]:
        if event.get("landing") is not None and event.get("target_midi") is not None:
            a = round(max(event["landing"], event["onset"]) * 100)
            b = round(max(event["landing"], event["end"]) * 100)
            held[a:b] = True
    new = old.copy()
    replace = voice & ~held & (f0 > 0)
    new[replace] = 69 + 12 * np.log2(f0[replace] / 440)
    return dict(old_render_midi=old, new_render_midi=new, render_midi=new,
                voiced=voice.copy(), held=held, replacement=replace,
                rmvpe_f0_hz=f0.copy(), confidence=gesture["confidence"].copy())


def qualify_attacks(boundaries: Iterable[float], pitch_midi: Any,
                    voiced: Any) -> dict[str, Any]:
    """Select model boundary proposals; never certify a word or articulation.

    Times are local seconds, not recording offsets. Same-pitch continuity
    also occurs in tremolo/vibrato; selections remain machine hypotheses.
    """
    pitch = _array(pitch_midi, "pitch_midi")
    voice = np.asarray(voiced, dtype=bool)
    if voice.shape != pitch.shape:
        raise ValueError("pitch and voicing must share a grid")
    proposals, selected = [], []
    for value in boundaries:
        time = float(value)
        if not np.isfinite(time):
            raise ValueError("boundary times must be finite")
        k = round(time * 100)
        delta = None
        if k < 5 or k + 5 >= len(pitch):
            reason = "edge"
        elif not np.all(voice[k - 3:k + 4]):
            reason = "voicing_boundary_or_gap"
        else:
            delta = float(np.median(pitch[k:k + 5]) - np.median(pitch[k - 5:k]))
            reason = "continuous_samepitch" if abs(delta) <= 0.75 else "pitch_transition"
        accepted = reason == "continuous_samepitch"
        proposals.append(dict(time_s=time, selected=accepted, reason=reason,
                              median_pitch_change_st=delta))
        if accepted:
            selected.append(time)
    return dict(proposals=proposals, selected_times_s=selected)


def render(pitch_midi: Any, voiced: Any, attack_times: Iterable[float] = ()) -> Any:
    """PCM16 practice audio, preserving the preferred two-stage quantization.

    Pitch interpolates only inside each voiced run. A linear 5 ms fade is
    inside the run; phase is global and continuous. Optional 24 ms attack
    notches multiply the already-quantized carrier, without touching pitch.
    """
    pitch = _array(pitch_midi, "pitch_midi")
    voice = np.asarray(voiced, dtype=bool)
    if voice.shape != pitch.shape:
        raise ValueError("pitch and voicing must share a grid")
    if not len(pitch):
        return np.zeros(0, dtype="<i2")
    n = len(pitch)
    grid = np.arange(n) * H
    time = np.arange(n * 160) / SR
    hz = 440 * 2 ** ((np.interp(time, grid, pitch) - 69) / 12)
    gate = np.zeros(len(time))
    for a, b in _spans(voice):
        lo, hi = a * 160, b * 160
        hz[lo:hi] = 440 * 2 ** ((np.interp(time[lo:hi], grid[a:b], pitch[a:b]) - 69) / 12)
        q = np.arange(hi - lo)
        gate[lo:hi] = np.maximum(0, np.minimum(1, np.minimum(q / 80, (hi - lo - 1 - q) / 80)))
    phase = np.cumsum(2 * np.pi * hz / SR)
    wave = (0.14 * (np.sin(phase) + 0.2 * np.sin(2 * phase)) * gate).astype(np.float32)
    pcm = np.clip(np.floor(wave.astype(np.float64) * 32768), -32768, 32767).astype("<i2")
    envelope = np.ones(len(pcm))
    width = round(0.012 * SR)
    for value in attack_times:
        when = float(value)
        if not np.isfinite(when) or when < 0 or when >= n * H:
            raise ValueError("attack times must lie inside the contour")
        center = round(when * SR)
        lo, hi = max(0, center - width), min(len(pcm), center + width + 1)
        distance = abs(np.arange(lo, hi) - center)
        envelope[lo:hi] = np.minimum(envelope[lo:hi], 0.5 - 0.5 * np.cos(np.pi * distance / width))
    return np.floor(pcm.astype(np.float64) * envelope).astype("<i2")


def to_notes(gesture: dict[str, Any], pitch_midi: Any, voiced: Any,
             attack_times: Iterable[float] = ()) -> dict[str, Any]:
    """A conservative timed note reading, with all voiced time represented.

    Frozen target events take precedence. An unlanded run uses short stable
    plateaus (three rows, <=3 cents/row, <=35 cents total) when available;
    otherwise it is one uncertain median-pitch event, never asserted silence.
    Repeated same-pitch attack proposals split events, and voicing gaps never
    merge. The full contour still carries slides, vibrato, and uncertainty.
    This projection is not an adopted Work melody or a grace-note policy.
    """
    pitch = _array(pitch_midi, "pitch_midi")
    voice = np.asarray(voiced, dtype=bool)
    if voice.shape != pitch.shape:
        raise ValueError("pitch and voicing must share a grid")
    attacks = list(attack_times)
    if any(not np.isfinite(x) or x < 0 or x >= len(pitch) * H for x in attacks):
        raise ValueError("attack times must lie inside the contour")
    notes, uncertain = [], []
    for a, b in _spans(voice):
        anchors = []
        for event in gesture.get("events", []):
            if event.get("landing") is not None and event.get("target_midi") is not None:
                row = round(event["onset"] * 100)
                if a <= row < b:
                    anchors.append((row, float(event["target_midi"]), "landing"))
        if not anchors:
            stable = np.ones(b - a, dtype=bool)
            if b - a > 1:
                step = abs(np.diff(pitch[a:b])) <= 0.03
                stable[1:] &= step
                stable[:-1] &= step
            for lo, hi in _spans(stable):
                part = pitch[a + lo:a + hi]
                if hi - lo >= 3 and np.ptp(part) <= 0.35:
                    value = float(np.median(part))
                    if not anchors or round(value) != round(anchors[-1][1]):
                        anchors.append((a + lo, value, "short_stable_run"))
        if anchors and all(item[2] == "short_stable_run" for item in anchors):
            uncertain.append(dict(
                start=float(a * H), end=float(b * H),
                reason="Short plateau candidates simplify an unlanded continuous gesture",
            ))
        if not anchors:
            anchors = [(a, float(np.median(pitch[a:b])), "unlanded_median")]
            uncertain.append(dict(start=a * H, end=b * H,
                                  reason="No discrete landing; median event covers a continuous gesture"))
        anchors.sort(key=lambda item: item[0])
        anchors[0] = (a, anchors[0][1], anchors[0][2])
        for index, (lo, value, method) in enumerate(anchors):
            hi = anchors[index + 1][0] if index + 1 < len(anchors) else b
            splits = sorted(set([lo, hi] + [round(t * 100) for t in attacks if lo < round(t * 100) < hi]))
            number = int(round(value))
            if not 0 <= number <= 127:
                raise ValueError("a voiced contour cannot be represented as a MIDI note")
            for first, end in zip(splits, splits[1:]):
                if first < end:
                    notes.append(dict(start=float(first * H), end=float(end * H), pitch=number,
                                      cents=float(100 * (value - number)), method=method,
                                      attack_proposal=bool(first != lo)))
    return dict(notes=notes, uncertain_regions=uncertain, rule=NOTE_SETTINGS["rule"])


def analyze(audio: Any, pesto_f0: Any, confidence: Any, level_db: Any,
            rmvpe_f0: Any, word_boundaries: Iterable[float] = ()) -> dict[str, Any]:
    """Compose frozen contour, pitch replacement, articulation and note reading.

    Inputs are already aligned/cropped to one target; runtime owns decoding,
    model acquisition and context. Returned times are target-local seconds.
    """
    gesture = segment(pesto_f0, confidence, level_db, audio)
    frames = replace_moving(gesture, rmvpe_f0)
    attacks = qualify_attacks(word_boundaries, frames["new_render_midi"], frames["voiced"])
    reading = to_notes(gesture, frames["new_render_midi"], frames["voiced"], attacks["selected_times_s"])
    return dict(rule=RULE, settings=dict(SETTINGS), gesture=gesture, frames=frames,
                attacks=attacks, notes=reading["notes"], note_reading=reading)
