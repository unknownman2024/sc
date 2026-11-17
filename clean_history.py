#!/usr/bin/env python3
"""
Safely wipe Git commit history while keeping current files.
Requires: Python 3.6+, Git installed.
"""
import subprocess
import sys
import os

# ============ CONFIGURATION ============
DEFAULT_BRANCH = "main"          # Change if your branch is "master"
BACKUP_BRANCH  = "backup-old-history"
REMOTE         = "origin"
# =======================================


def run(cmd, check=True, silent=False):
    """Run a shell command and return output."""
    if not silent:
        print(f"  ▶ {cmd}")
    res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if not silent:
        if res.stdout.strip():
            print(f"    {res.stdout.strip()}")
        if res.stderr.strip():
            print(f"    {res.stderr.strip()}")
    if check and res.returncode != 0:
        print(f"\n❌ Command failed (exit {res.returncode}). Aborting.")
        sys.exit(1)
    return res


def main():
    # 0. Basic sanity checks
    if not os.path.isdir(".git"):
        print("❌ Not a Git repository root. cd into your repo first.")
        sys.exit(1)

    # 1. Ensure working tree is clean (no uncommitted changes)
    status = run("git status --porcelain", silent=True).stdout.strip()
    if status:
        print("❌ You have uncommitted changes. Commit or stash them first.")
        sys.exit(1)

    print("\n⚠️  This will permanently delete ALL old commits on the remote.")
    print(f"   A backup will be pushed to branch: {BACKUP_BRANCH}")
    confirm = input("   Type 'WIPE' to continue: ")
    if confirm != "WIPE":
        print("Aborted.")
        sys.exit(0)

    # 2. Create backup branch on remote BEFORE any destructive action
    print(f"\n📦 Creating backup branch '{BACKUP_BRANCH}'...")
    run(f"git branch {BACKUP_BRANCH} {DEFAULT_BRANCH}")
    run(f"git push {REMOTE} {BACKUP_BRANCH}")

    # 3. Create orphan branch (no parent commits)
    print("\n🔄 Creating orphan branch...")
    run(f"git checkout --orphan clean_temp")

    # 4. Stage everything and commit
    run("git add -A")
    run('git commit -m "Initial commit (clean history)"')

    # 5. Delete old default branch locally and rename temp branch
    run(f"git branch -D {DEFAULT_BRANCH}")
    run(f"git branch -m {DEFAULT_BRANCH}")

    # 6. Force-push SAFELY using --force-with-lease
    print("\n🚀 Force-pushing with lease protection...")
    run(f"git push --force-with-lease {REMOTE} {DEFAULT_BRANCH}")

    print("\n✅ Done. Your repository now has a single clean commit.")
    print(f"   Old history is preserved on branch '{BACKUP_BRANCH}' (remote).")
    print("   ⚠️  Collaborators must re-clone or run:")
    print(f"      git fetch --all && git reset --hard {REMOTE}/{DEFAULT_BRANCH}")


if __name__ == "__main__":
    main()
