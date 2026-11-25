"""Deploy notebook Fabric dengan fabric-cicd.

File ini identik di repo sarailmax-3-dev dan sarailmax-3-test. Target deploy
ditentukan dari nama repository:
    sarailmax-3-dev  -> workspace test
    sarailmax-3-test -> workspace prod

Pemakaian:
    python .deploy/deploy.py --check   # validasi saja, tanpa memanggil Fabric
    python .deploy/deploy.py           # deploy notebook ke workspace target

Saat deploy, kredensial service principal dibaca dari AZURE_TENANT_ID,
AZURE_CLIENT_ID, dan AZURE_CLIENT_SECRET, sedangkan ID workspace target dari
FABRIC_WORKSPACE_ID. Script ini tidak membuat file apa pun.
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Repo sumber -> workspace target.
TARGETS = {"dev": "test", "test": "prod"}

# ID workspace Fabric (bukan rahasia), dipakai untuk mencegah salah target.
WORKSPACE_IDS = {
    "dev": "7a289e5a-6fae-4253-be62-e2fa4c3e9a5e",
    "test": "1bc2ff77-af62-4dcc-8767-9f2e32f6f22c",
    "prod": "579d2ad1-3d06-4fbd-99b0-7ee633c6ced8",
}

# Hanya notebook yang di-deploy; lakehouse dan environment tidak disentuh.
ITEM_TYPES = ["Notebook"]

SPN_VARS = ("AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET")
CONFLICT_MARKER = re.compile(r"^(<{7}|>{7})( |$)", re.MULTILINE)


def fail(message):
    sys.exit(f"ERROR: {message}")


def resolve_target():
    """Menentukan sumber dan target dari nama repository."""
    repo = os.getenv("GITHUB_REPOSITORY", REPO_ROOT.name).split("/")[-1]
    source = repo.rsplit("-", 1)[-1].lower()
    if source not in TARGETS:
        fail(f"repository '{repo}' tidak punya target deploy.")
    return source, TARGETS[source]


def meta_blocks(text):
    """Mengambil setiap blok '# META' pada notebook-content.py."""
    block = []
    for line in text.splitlines() + [""]:
        if line == "# META" or line.startswith("# META "):
            block.append(line[len("# META"):])
        elif block:
            yield "\n".join(block)
            block = []


def check_notebooks():
    """Validasi struktur notebook tanpa memanggil Fabric."""
    folders = sorted(
        path
        for path in REPO_ROOT.rglob("*.Notebook")
        if path.is_dir()
        and not any(
            part.startswith(".")
            for part in path.relative_to(REPO_ROOT).parts
        )
    )
    if not folders:
        fail("tidak ada folder *.Notebook. Deploy dibatalkan agar "
             "notebook di workspace target tidak terhapus.")
    for folder in folders:
        try:
            platform = json.loads((folder / ".platform").read_text("utf-8"))
            if platform["metadata"]["type"] != "Notebook":
                fail(f"{folder.name}: .platform bukan bertipe Notebook.")
            text = (folder / "notebook-content.py").read_text("utf-8")
            if CONFLICT_MARKER.search(text):
                fail(f"{folder.name}: masih ada penanda konflik merge.")
            for block in meta_blocks(text):
                json.loads(block)
        except (OSError, ValueError, KeyError) as exc:
            fail(f"{folder.name}: {exc}")
        print(f"OK      {folder.name}")
    return folders


def deploy(target):
    """Publish notebook ke workspace target dan hapus notebook yatim."""
    from azure.identity import ClientSecretCredential
    from fabric_cicd import (
        FabricWorkspace,
        disable_file_logging,
        publish_all_items,
        unpublish_all_orphan_items,
    )

    expected_id = WORKSPACE_IDS[target]
    workspace_id = (os.getenv("FABRIC_WORKSPACE_ID") or expected_id).strip()
    if workspace_id.lower() != expected_id:
        fail(f"FABRIC_WORKSPACE_ID bukan workspace {target} ({expected_id}).")
    missing = [name for name in SPN_VARS if not os.getenv(name)]
    if missing:
        fail(f"secret belum diisi di GitHub Environment: {', '.join(missing)}")

    disable_file_logging()  # log hanya tampil di konsol, tanpa file log
    workspace = FabricWorkspace(
        workspace_id=workspace_id,
        environment=target,
        repository_directory=str(REPO_ROOT),
        item_type_in_scope=ITEM_TYPES,
        token_credential=ClientSecretCredential(
            tenant_id=os.environ["AZURE_TENANT_ID"],
            client_id=os.environ["AZURE_CLIENT_ID"],
            client_secret=os.environ["AZURE_CLIENT_SECRET"],
        ),
    )
    publish_all_items(workspace)
    unpublish_all_orphan_items(workspace)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="validasi repository saja, tanpa deploy",
    )
    args = parser.parse_args()

    source, target = resolve_target()
    print(f"Sumber  : {source}  ->  Target : {target}")
    check_notebooks()

    if args.check:
        output = os.getenv("GITHUB_OUTPUT")
        if output:
            with open(output, "a", encoding="utf-8") as handle:
                handle.write(f"target={target}\n")
        print("Validasi selesai, tidak ada deploy yang dijalankan.")
        return
    deploy(target)


if __name__ == "__main__":
    main()
