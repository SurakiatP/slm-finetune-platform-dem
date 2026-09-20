"""Download reviewed public snapshots only; no model weights or credentials."""

from __future__ import annotations

import argparse
from pathlib import Path

from huggingface_hub import snapshot_download

from scripts.prepare_template_qa import REVISION, TOKENIZER_REVISION
from scripts.prepare_template_thai import SOURCES as THAI_SOURCES
from scripts.prepare_template_tools import SOURCES as TOOL_SOURCES


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data/template-catalog"))
    args = parser.parse_args()
    for source in THAI_SOURCES.values():
        snapshot_download(
            repo_id=source["repo"],
            repo_type="dataset",
            revision=source["revision"],
            allow_patterns=["README.md", source["subdirectory"] + "/*.parquet"],
            local_dir=args.root / "sources" / source["directory"],
            token=False,
        )
    for directory, source in TOOL_SOURCES.items():
        snapshot_download(
            repo_id=source["repo"],
            repo_type="dataset",
            revision=source["revision"],
            allow_patterns=["README.md", *source["files"]],
            local_dir=args.root / "sources" / directory,
            token=False,
        )
    snapshot_download(
        repo_id="nvidia/TechQA-RAG-Eval",
        repo_type="dataset",
        revision=REVISION,
        allow_patterns=["README.md", "train.json"],
        local_dir=args.root / "sources/techqa",
        token=False,
    )
    snapshot_download(
        repo_id="Qwen/Qwen2.5-1.5B-Instruct",
        revision=TOKENIZER_REVISION,
        allow_patterns=[
            "README.md",
            "LICENSE",
            "tokenizer.json",
            "tokenizer_config.json",
            "config.json",
            "merges.txt",
            "vocab.json",
        ],
        local_dir=args.root / "tokenizer",
        token=False,
    )


if __name__ == "__main__":
    main()
