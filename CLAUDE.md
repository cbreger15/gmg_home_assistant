# gmg_home_assistant

## Repo hygiene

After a PR merges, and at the start of a session, run `bash .agent/hygiene.sh`. It removes worktrees, local branches, and GitHub branches whose work is already on GitHub, and lists anything it kept on purpose (uncommitted changes, commits that exist only on this PC). Preview with `bash .agent/hygiene.sh --dry-run`. Commit and push before ending a session in a worktree, so hygiene can clean it up later.
