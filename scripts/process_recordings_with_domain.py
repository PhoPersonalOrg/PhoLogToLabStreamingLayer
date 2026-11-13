"""Batch-transcribe recordings with PhoLog private domain vocabulary injected.

Thin wrapper around whisper-timestamped ``scripts.process_recordings`` that always
passes hotwords, initial_prompt, and result_corrector from
``phologtolabstreaminglayer.domain_vocabulary``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _ensure_whisper_scripts_on_path() -> Path:
    """Put the whisper-timestamped repo root on sys.path so ``scripts.*`` imports work."""
    import whisper_timestamped

    root = Path(whisper_timestamped.__file__).resolve().parent.parent
    root_str = str(root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)
    return root


def main(argv: list[str] | None = None) -> int:
    _ensure_whisper_scripts_on_path()

    from scripts.process_recordings import (  # type: ignore[import-not-found]
        DEFAULT_FORMAT,
        _parse_video_extensions,
        process_recordings,
    )
    from whisper_timestamped.recording_formats import get_format, list_format_ids

    from phologtolabstreaminglayer.domain_vocabulary import (
        apply_domain_corrections_to_result,
        get_hotwords,
        get_initial_prompt,
    )

    known_formats = ", ".join(list_format_ids())
    parser = argparse.ArgumentParser(
        description=(
            "Batch-transcribe recordings with PhoLog domain phrase boosting. "
            "Provide at most one of --filelist or --recordings_dir; "
            "when neither is set, uses the format's default input mode."
        )
    )
    parser.add_argument(
        "--format",
        "-f",
        dest="format_id",
        default=DEFAULT_FORMAT,
        choices=list_format_ids(),
        help=f"Recordings format id (known: {known_formats}; default: {DEFAULT_FORMAT})",
    )
    parser.add_argument(
        "--filelist",
        type=Path,
        default=None,
        help="Filelist CSV path (sets filelist_csv; mutually exclusive with --recordings_dir)",
    )
    parser.add_argument(
        "--recordings_dir",
        type=Path,
        default=None,
        help="Directory of media to glob (mutually exclusive with --filelist)",
    )
    parser.add_argument(
        "--output_dir",
        type=Path,
        default=None,
        help="Transcript output directory (default: format's default_output_dir)",
    )
    parser.add_argument(
        "--video_extensions",
        type=_parse_video_extensions,
        default=None,
        help="Comma-separated media extensions for directory mode (e.g. .m4a,.caf)",
    )
    args = parser.parse_args(argv)

    if args.filelist is not None and args.recordings_dir is not None:
        parser.error("provide at most one of --filelist and --recordings_dir")

    fmt = get_format(args.format_id)
    if args.filelist is not None:
        process_recordings_kwargs = {
            "filelist_csv": args.filelist,
            "output_dir": (
                args.output_dir
                if args.output_dir is not None
                else fmt.default_output_dir
            ),
        }
    elif args.recordings_dir is not None:
        process_recordings_kwargs = {
            "recordings_dir": args.recordings_dir,
            "output_dir": (
                args.output_dir
                if args.output_dir is not None
                else fmt.default_output_dir
            ),
            "video_extensions": (
                args.video_extensions
                if args.video_extensions is not None
                else list(fmt.media_extensions)
            ),
        }
    else:
        process_recordings_kwargs = fmt.process_recordings_kwargs(
            output_dir=args.output_dir,
            video_extensions=args.video_extensions,
        )

    print(
        f"Active recordings format: {fmt.id} ({fmt.label}) -> "
        f"{process_recordings_kwargs} (with PhoLog domain vocabulary)"
    )

    try:
        output_files = process_recordings(
            **process_recordings_kwargs,
            backend="crisperwhisper",
            model_name="turbo",
            crisper_mode="verbatim",
            crisper_runtime="auto",
            hotwords=get_hotwords(),
            initial_prompt=get_initial_prompt(),
            result_corrector=apply_domain_corrections_to_result,
        )
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130

    print(f"All processing complete! output_files: {output_files}\n\ndone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
