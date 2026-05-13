# Registry Forge — complete site regeneration

**Use this zip if your last few uploads of `RegistryForge_site_updates.zip`
didn't actually take effect on the live site.** This package contains
**every file** that should be in the docs portion of your `BoyceLab/
RegistryForge` repo — drop it in as a full replacement and there is no
way for stale files to linger.

## What's inside

```
RegistryForge_full_site/
├── mkdocs.yml                   # site config — replaces yours at repo root
├── drug_repurposing.py          # 5 Python modules — at repo root
├── device_dashboard.py
├── exposure_extraction.py
├── exposure_dashboard.py
├── note_dashboard.py
└── docs/                        # complete docs tree — REPLACES yours entirely
    ├── index.md                 # home page (with CDC funding banner at top,
    │                              6-demo hero, no "Chunked CSV", etc.)
    ├── about.md
    ├── architecture.md
    ├── cohort-eda.md
    ├── dashboard.md
    ├── demo.md
    ├── device-dashboard.md
    ├── devices.md
    ├── downloads.md
    ├── drug-repurposing.md
    ├── exposure-dashboard.md
    ├── exposure-extraction.md
    ├── note-dashboard.md        # NEW
    ├── note-extraction.md
    ├── (etc. — 37 total .md pages)
    ├── stages/                  # 6 stage-detail pages
    ├── stylesheets/extra.css    # custom CSS including new .rf-funding banner
    └── assets/                  # 30+ files: Python downloads + demo HTMLs
```

## Deployment (the foolproof way)

The reason recent uploads didn't render is almost certainly one of:

1. **Drag-and-drop uploads in GitHub's web UI add files alongside
   existing ones; they don't delete the old ones.** If your old
   `docs/index.md` still exists in the repo, the new one may not be
   overwriting it cleanly. The fix is to delete first, then upload.
2. **Browser cache.** Even after a successful push, browsers can hold
   on to the old HTML/CSS for hours. Hard refresh (Cmd-Shift-R or
   Ctrl-Shift-R) is required.
3. **The GitHub Pages build action may have failed.** Check the Actions
   tab on the repo after pushing — if the most recent run has a red ✗,
   click into it to see the MkDocs error.

### Step-by-step

**1. In your local clone of `BoyceLab/RegistryForge`:**

```bash
cd ~/path/to/BoyceLab-RegistryForge   # wherever your local clone lives

# Delete the existing docs folder + mkdocs.yml — this is what guarantees
# no stale files linger. Your .git/, .github/, README.md, LICENSE, etc.
# are untouched.
rm -rf docs/
rm -f mkdocs.yml
```

**2. Unzip this package directly into the repo:**

```bash
unzip /path/to/RegistryForge_full_site.zip
# Move everything inside the wrapper folder up to the repo root
mv RegistryForge_full_site/* .
mv RegistryForge_full_site/.[!.]* . 2>/dev/null || true
rmdir RegistryForge_full_site
```

**3. Verify the file count:**

```bash
ls docs/*.md | wc -l        # should be 37
ls docs/assets/ | wc -l     # should be around 30
ls mkdocs.yml docs/stylesheets/extra.css   # both should exist
```

**4. Commit and push:**

```bash
git add -A
git status                  # confirm the changes are what you expect
git commit -m "Regenerate site: add CDC funding banner, note dashboard, fix terminology"
git push origin main
```

**5. Watch the build:**

Go to `https://github.com/BoyceLab/RegistryForge/actions` and confirm
the most recent workflow run shows a green ✓. If it shows a red ✗,
click into the run, find the failing step, and paste me the error.

**6. Hard refresh the live site:**

After the action completes (1-3 minutes), open
`https://boycelab.github.io/RegistryForge/` and do a hard refresh:
- macOS Safari / Chrome / Firefox: Cmd-Shift-R
- Windows / Linux Chrome / Firefox: Ctrl-Shift-R
- Or open in incognito / private window

You should now see, in order from top of page down:

- A gold-bordered banner reading "Funding acknowledgement. This work
  was supported by the Centers for Disease Control and Prevention
  grant # R01-TS000341."
- The Registry Forge hero with **no "Chunked CSV" badge**
- Eight CTA buttons: Quickstart, Cohort dashboard, Cohort EDA, Note
  extraction, Drug repurposing report, Device dashboard, Exposure
  dashboard, Downloads
- Below the hero, a "Live demos — Six privacy-safe demos" section
  with six cards in a grid (cohort dashboard, EDA, note extraction,
  drug repurposing report, device dashboard, exposure dashboard)
- Left sidebar with a "Live demos" group containing the six entries

## If something still isn't right after step 6

Paste me a screenshot or the exact URL and I'll diagnose. Likely
candidates:

- **CDN cache:** GitHub Pages uses a Fastly CDN; some users wait
  5-10 minutes for global propagation
- **Workflow failure:** Action log will say why
- **Old browser cache:** Try incognito as a control
