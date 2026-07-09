"""
Push local data/ folder to HuggingFace private dataset.

Usage:
  python scripts/push_data.py

Run this after any ingestion script to sync new data to HuggingFace.
Requires HF_TOKEN in .env.

First-time setup:
  1. Create a private dataset on HuggingFace: https://huggingface.co/new-dataset
  2. Set hf_dataset_repo in config/settings.yaml to "username/dataset-name"
  3. Run this script
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

    data_dir = Path("data")
    if not data_dir.exists():
        print("ERROR: data/ directory not found. Run scripts/ingest_goodreads.py first.")
        sys.exit(1)

    try:
        from huggingface_hub import HfApi
    except ImportError:
        print("ERROR: Run: pip install huggingface_hub")
        sys.exit(1)

    api = HfApi(token=hf_token)

    try:
        api.repo_info(repo_id=repo_id, repo_type="dataset")
    except Exception:
        print(f"Creating dataset repo: {repo_id}")
        api.create_repo(repo_id=repo_id, repo_type="dataset", private=True)

    print(f"Pushing data/ to HuggingFace: {repo_id}")

    jsonl_files = list(data_dir.rglob("*.jsonl")) + list(data_dir.rglob("*.json"))
    print(f"  Files to upload: {len(jsonl_files)}")

    api.upload_folder(
        folder_path=str(data_dir),
        repo_id=repo_id,
        repo_type="dataset",
        commit_message="Update reading data",
        ignore_patterns=["*.gitattributes", ".gitattributes", "*.DS_Store", ".cache/*", ".cache"],
    )

    print("Done. Data synced to HuggingFace.")


if __name__ == "__main__":
    main()
