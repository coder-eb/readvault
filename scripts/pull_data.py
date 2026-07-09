"""
Pull processed JSONL data from HuggingFace private dataset into local data/.

Usage:
  python scripts/pull_data.py

Run this on any new device after git clone to get the processed data.
Requires HF_TOKEN in .env and the dataset to already exist on HuggingFace.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def load_env():
    env_path = Path(".env")
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


def main():
    load_env()

    hf_token = os.environ.get("HF_TOKEN", "").strip()
    if not hf_token:
        print("ERROR: HF_TOKEN not set. Add it to .env (see .env.example)")
        sys.exit(1)

    import yaml
    with open("config/settings.yaml") as f:
        settings = yaml.safe_load(f) or {}

    repo_id = settings.get("hf_dataset_repo", "").strip()
    if not repo_id or repo_id.startswith("your-"):
        print("ERROR: Set hf_dataset_repo in config/settings.yaml")
        print("  Example: hf_dataset_repo: ebran/reading-data")
        sys.exit(1)

    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print("ERROR: Run: pip install huggingface_hub")
        sys.exit(1)

    print(f"Pulling data from HuggingFace: {repo_id}")
    snapshot_download(
        repo_id=repo_id,
        repo_type="dataset",
        local_dir="data",
        token=hf_token,
        ignore_patterns=["*.gitattributes", ".gitattributes", "README.md"],
    )
    print("Done. Data available at: data/")


if __name__ == "__main__":
    main()
