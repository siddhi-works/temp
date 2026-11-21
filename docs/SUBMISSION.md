# Putting this code on another repository

For when the organisers want the code as a branch or a folder in their repository. Nothing here is done yet: it is ready.

## What is safe to copy

Only files tracked by git are copied. The script reads `git ls-files`, so `.env`, keys, databases, `node_modules`, `.venv` and build output cannot go with it.

## Option A: a branch with the code at the top level

```powershell
git remote add organizer <organiser-repository-url>
git push organizer main:<branch-name>        # for example main:MolecuLens-saathi
```

This puts the full history on a new branch and leaves their `main` untouched.

## Option B: a folder inside their repository

```powershell
# 1. clone their repository and create your branch
git clone <organiser-repository-url> ..\organiser && cd ..\organiser
git checkout -b MolecuLens-saathi

# 2. copy a clean snapshot into a folder
..\saathi-campus\scripts\export-submission.ps1 -Dest . -SubFolder saathi-campus

# 3. check what will be added, then commit and push
git status
git add saathi-campus && git commit -m "Saathi Campus by team MolecuLens" && git push -u origin MolecuLens-saathi
```

## Before you push, check

- [ ] `git status` shows no `.env` or key files.
- [ ] The README opens cleanly on GitHub and the diagrams render.
- [ ] You use the branch name the organisers asked for.
- [ ] You never push to their `main` unless they tell you to.

## Deployment is unaffected

Render deploys from your own repository. The organiser's copy is a snapshot for judging. After later changes, push to both. To deploy from the organiser's repository instead, set Render's *Root Directory* to `<subfolder>/code` (Option B) or `code` (Option A), and make sure Render can read that repository.
