#!/usr/bin/env python3
"""
Speech-to-Text Benchmark Tool for DecisionAI.

Evaluates STT providers (faster-whisper, Gemini, Sarvam, Deepgram) against
human-corrected reference transcripts with Hindi (Devanagari) and Hinglish normalization.
Computes Word Error Rate (WER) and prints a formatted comparison table.

Usage:
    # Run built-in demo benchmark
    python scripts/benchmark.py --demo

    # Benchmark a specific audio file with a reference transcript
    python scripts/benchmark.py --audio audio.wav --reference ref.txt --providers faster_whisper gemini

    # Benchmark all WAV and TXT pairs in a directory
    python scripts/benchmark.py --dir ./benchmark_data/ --mode hinglish
"""

import argparse
import asyncio
from dataclasses import dataclass
import os
from pathlib import Path
import sys
import time

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.config import get_settings
from backend.services.stt import (
    PROVIDERS,
    compute_word_error_rate,
    get_transcription_provider,
    normalize_hindi,
    normalize_hinglish,
)
from backend.services.stt.base import TranscriptionProvider


@dataclass
class BenchmarkRow:
    provider: str
    audio_name: str
    mode: str
    ref_words: int
    hyp_words: int
    wer_percent: float
    subs: int
    dels: int
    inss: int
    latency_sec: float
    status: str = "OK"


def print_comparison_table(rows: list[BenchmarkRow]):
    """Print an aligned ASCII comparison table of benchmark results."""
    if not rows:
        print("\nNo benchmark results to display.\n")
        return

    col_widths = {
        "provider": 16,
        "audio": 22,
        "mode": 10,
        "ref": 9,
        "hyp": 9,
        "wer": 10,
        "sub": 5,
        "del": 5,
        "ins": 5,
        "latency": 11,
        "status": 8,
    }

    sep_line = "+" + "+".join("-" * (w + 2) for w in col_widths.values()) + "+"
    header_line = (
        "| "
        + " | ".join(
            [
                f"{'Provider':<{col_widths['provider']}}",
                f"{'Audio / Case':<{col_widths['audio']}}",
                f"{'Mode':<{col_widths['mode']}}",
                f"{'Ref Wds':>{col_widths['ref']}}",
                f"{'Hyp Wds':>{col_widths['hyp']}}",
                f"{'WER (%)':>{col_widths['wer']}}",
                f"{'Sub':>{col_widths['sub']}}",
                f"{'Del':>{col_widths['del']}}",
                f"{'Ins':>{col_widths['ins']}}",
                f"{'Latency':>{col_widths['latency']}}",
                f"{'Status':<{col_widths['status']}}",
            ]
        )
        + " |"
    )

    print("\n" + "=" * len(sep_line))
    print("                      DecisionAI Speech-to-Text Benchmark")
    print("=" * len(sep_line))
    print(sep_line)
    print(header_line)
    print(sep_line)

    for r in rows:
        row_str = (
            "| "
            + " | ".join(
                [
                    f"{r.provider[:col_widths['provider']]:<{col_widths['provider']}}",
                    f"{r.audio_name[:col_widths['audio']]:<{col_widths['audio']}}",
                    f"{r.mode[:col_widths['mode']]:<{col_widths['mode']}}",
                    f"{r.ref_words:>{col_widths['ref']}}",
                    f"{r.hyp_words:>{col_widths['hyp']}}",
                    f"{r.wer_percent:>9.2f}%",
                    f"{r.subs:>{col_widths['sub']}}",
                    f"{r.dels:>{col_widths['del']}}",
                    f"{r.inss:>{col_widths['ins']}}",
                    f"{r.latency_sec:>9.2f}s",
                    f"{r.status[:col_widths['status']]:<{col_widths['status']}}",
                ]
            )
            + " |"
        )
        print(row_str)

    print(sep_line)

    # Provider Summary
    provider_stats: dict[str, list[BenchmarkRow]] = {}
    for r in rows:
        if r.status == "OK":
            provider_stats.setdefault(r.provider, []).append(r)

    if provider_stats:
        print("\n=== Provider Average Performance ===")
        for prov, p_rows in sorted(provider_stats.items()):
            avg_wer = sum(x.wer_percent for x in p_rows) / len(p_rows)
            avg_lat = sum(x.latency_sec for x in p_rows) / len(p_rows)
            print(f"  - {prov:<16}: Average WER = {avg_wer:6.2f}% | Avg Latency = {avg_lat:5.2f}s ({len(p_rows)} runs)")
    print()


def run_demo_benchmark():
    """
    Run self-contained demo test cases covering:
    1. Pure Devanagari Hindi meeting dialogue.
    2. Conversational Hinglish code-switching with English technical terms.
    3. Proper nouns and brand names requiring glossary disambiguation.
    """
    demo_cases = [
        {
            "name": "hindi_standup_1",
            "lang_mode": "hindi",
            "reference": "नमस्ते दोस्तों आज की स्टैंडअप मीटिंग शुरू करते हैं। कल हमने पेमेंट्स एपीआई का काम पूरा कर लिया था।",
            "hypotheses": {
                "faster_whisper": "नमस्ते दोस्तों आज की स्टैंडअप मीटिंग शुरू करते हैं। कल हमने पेमेंट्स एपीआई का काम पूरा कर लिया था।",
                "gemini": "नमस्ते दोस्तों आज की स्टैंडअप मीटिंग शुरू करते हैं। कल हमने पेमेंट्स API का काम पूरा कर लिया था।",
                "sarvam": "नमस्ते दोस्तों आज की स्टैंडअप मीटिंग शुरू करते हैं। कल हमने पेमेंट्स एपीआई का काम पूरा कर लिया था।",
                "deepgram": "नमस्ते दोस्तों आज की स्टैंड अप मीटिंग शुरू करते हैं कल हमने पेमेंट एपीआई का काम पूरा कर लिया था",
            },
        },
        {
            "name": "hinglish_code_mix",
            "lang_mode": "hinglish",
            "reference": "bhai kal ka deployment smooth tha par backend server pe memory leak detect hua hai.",
            "hypotheses": {
                "faster_whisper": "bhai kal ka deployment smooth tha par backend server pe memory leak detect hua hai.",
                "gemini": "Bhai kal ka deployment smooth tha, par backend server pe memory leak detect hua hai.",
                "sarvam": "bhai kal ka deployment smooth tha par backend server pe memory leak detect hua hai.",
                "deepgram": "bhai kal ka deployement smooth tha par back end server pe memory leak detect hua h",
            },
        },
        {
            "name": "glossary_terms",
            "lang_mode": "hinglish",
            "reference": "DecisionAI me ham Google Meet audio capture karte hain aur UPI Aadhaar integration test karenge.",
            "hypotheses": {
                "faster_whisper": "DecisionAI me ham Google Meet audio capture karte hain aur UPI Aadhaar integration test karenge.",
                "gemini": "DecisionAI me hum Google Meet audio capture karte hain aur UPI Aadhaar integration test karenge.",
                "sarvam": "DecisionAI me ham Google Meet audio capture karte hain aur UPI Aadhaar integration test karenge.",
                "deepgram": "Decision AI me hum Google Meet audio capture karte hai aur upi aadhar integration test karenge",
            },
        },
    ]

    rows: list[BenchmarkRow] = []

    for case in demo_cases:
        ref = case["reference"]
        mode = case["lang_mode"]
        for provider, hyp in case["hypotheses"].items():
            t0 = time.time()
            wer_res = compute_word_error_rate(ref, hyp, mode=mode)
            elapsed = time.time() - t0 + 0.05  # Simulated fast calculation latency
            rows.append(
                BenchmarkRow(
                    provider=provider,
                    audio_name=case["name"],
                    mode=mode,
                    ref_words=wer_res.ref_words,
                    hyp_words=wer_res.hyp_words,
                    wer_percent=wer_res.wer_percent,
                    subs=wer_res.substitutions,
                    dels=wer_res.deletions,
                    inss=wer_res.insertions,
                    latency_sec=elapsed,
                    status="OK",
                )
            )

    print_comparison_table(rows)


async def run_live_benchmark(
    audio_paths: list[Path],
    ref_paths: list[Path],
    provider_names: list[str],
    mode: str,
    glossary: list[str],
):
    """Run live audio transcription through configured providers and compute WER."""
    settings = get_settings()
    rows: list[BenchmarkRow] = []

    # Map audio files to reference texts
    pairs: list[tuple[Path, Path]] = []
    if len(audio_paths) == 1 and len(ref_paths) == 1:
        pairs.append((audio_paths[0], ref_paths[0]))
    else:
        for a_path in audio_paths:
            matching_ref = next(
                (r for r in ref_paths if r.stem == a_path.stem), None
            )
            if matching_ref:
                pairs.append((a_path, matching_ref))
            else:
                print(f"Warning: No matching reference file found for {a_path.name}")

    if not pairs:
        print("Error: No matching audio and reference transcript pairs found.")
        return

    for prov_name in provider_names:
        try:
            provider = get_transcription_provider(prov_name, settings=settings)
        except Exception as e:
            print(f"Could not initialize provider '{prov_name}': {e}")
            continue

        is_avail = provider.is_available()
        print(f"\n--- Testing Provider: {prov_name} (Available: {is_avail}) ---")

        for audio_file, ref_file in pairs:
            ref_text = ref_file.read_text(encoding="utf-8").strip()

            if not is_avail:
                rows.append(
                    BenchmarkRow(
                        provider=prov_name,
                        audio_name=audio_file.name,
                        mode=mode,
                        ref_words=len(ref_text.split()),
                        hyp_words=0,
                        wer_percent=100.0,
                        subs=0,
                        dels=len(ref_text.split()),
                        inss=0,
                        latency_sec=0.0,
                        status="UNAVAIL",
                    )
                )
                continue

            t_start = time.time()
            try:
                res = await provider.transcribe(
                    audio_file,
                    speaker="Benchmark",
                    glossary=glossary,
                )
                latency = time.time() - t_start
                hyp_text = res.text

                # Evaluate WER
                wer_res = compute_word_error_rate(ref_text, hyp_text, mode=mode)
                rows.append(
                    BenchmarkRow(
                        provider=prov_name,
                        audio_name=audio_file.name,
                        mode=mode,
                        ref_words=wer_res.ref_words,
                        hyp_words=wer_res.hyp_words,
                        wer_percent=wer_res.wer_percent,
                        subs=wer_res.substitutions,
                        dels=wer_res.deletions,
                        inss=wer_res.insertions,
                        latency_sec=latency,
                        status="OK",
                    )
                )
            except Exception as e:
                latency = time.time() - t_start
                print(f"  Error transcribing {audio_file.name} with {prov_name}: {e}")
                rows.append(
                    BenchmarkRow(
                        provider=prov_name,
                        audio_name=audio_file.name,
                        mode=mode,
                        ref_words=len(ref_text.split()),
                        hyp_words=0,
                        wer_percent=100.0,
                        subs=0,
                        dels=len(ref_text.split()),
                        inss=0,
                        latency_sec=latency,
                        status="ERROR",
                    )
                )

    print_comparison_table(rows)


def main():
    parser = argparse.ArgumentParser(
        description="DecisionAI Speech-to-Text Benchmark Tool"
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run built-in multi-provider Hindi & Hinglish benchmark demonstration",
    )
    parser.add_argument(
        "--audio",
        type=str,
        help="Path to single audio WAV file",
    )
    parser.add_argument(
        "--reference",
        type=str,
        help="Path to reference transcript TXT file",
    )
    parser.add_argument(
        "--dir",
        type=str,
        help="Directory containing matching .wav and .txt files for batch evaluation",
    )
    parser.add_argument(
        "--mode",
        type=str,
        default="hinglish",
        choices=["hindi", "hinglish", "general", "raw"],
        help="Text normalization mode (default: hinglish)",
    )
    parser.add_argument(
        "--providers",
        nargs="+",
        default=["faster_whisper", "gemini", "sarvam", "deepgram"],
        help="Providers to benchmark",
    )
    parser.add_argument(
        "--glossary",
        type=str,
        default="",
        help="Comma-separated glossary terms to pass as hints",
    )

    args = parser.parse_args()

    if args.demo or (not args.audio and not args.dir):
        print("Running DecisionAI Speech-to-Text Benchmark Demo...")
        run_demo_benchmark()
        return

    # Parse audio and reference files
    audio_files: list[Path] = []
    ref_files: list[Path] = []

    if args.dir:
        dir_path = Path(args.dir)
        if not dir_path.is_dir():
            print(f"Error: Directory '{args.dir}' does not exist.")
            sys.exit(1)
        audio_files = sorted(dir_path.glob("*.wav"))
        ref_files = sorted(dir_path.glob("*.txt"))
    elif args.audio and args.reference:
        audio_p = Path(args.audio)
        ref_p = Path(args.reference)
        if not audio_p.exists():
            print(f"Error: Audio file '{args.audio}' not found.")
            sys.exit(1)
        if not ref_p.exists():
            print(f"Error: Reference file '{args.reference}' not found.")
            sys.exit(1)
        audio_files = [audio_p]
        ref_files = [ref_p]

    glossary_terms = [g.strip() for g in args.glossary.split(",") if g.strip()]

    asyncio.run(
        run_live_benchmark(
            audio_paths=audio_files,
            ref_paths=ref_files,
            provider_names=args.providers,
            mode=args.mode,
            glossary=glossary_terms,
        )
    )


if __name__ == "__main__":
    main()
