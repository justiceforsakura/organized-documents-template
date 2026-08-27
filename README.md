# 🗂️ Organized Documents Template

> Free document folder structure for pro se litigants, 
> overwhelmed families, and anyone drowning in paperwork.

Built by Anna Nyulund — aerospace engineer, pro se litigant, 
and person who once spent an entire day turning a 
floor-to-ceiling mail room into a living room.
This is the digital equivalent.

---

## What's included

- `create_organized_folders.sh` — creates 300+ organized folders instantly
- `color_folders.sh` — color codes folders by depth level so you always know where you are
- `organized-docs` — a Python command that reads a folder of unsorted documents and files them into the structure above, renamed and logged

---

## How to use

**Step 1: Create the folder structure**
```bash
bash create_organized_folders.sh
```

**Step 2: Color code by depth**
```bash
bash color_folders.sh
```

**To run it on your existing "Organized Documents Folder" — just open color_folders.sh in VS Code and change this line:**
```bash
BASE="$HOME/Documents/Organized Documents CLEAN"
```
To:
```bash
BASE="$HOME/Documents/Organized Documents Folder"
```
---

## Step 3: Sort the pile (`organized-docs`)

Install it once, from this folder:

```bash
pip install .
```

Then point it at your unsorted documents. **Nothing moves until you say `--apply`** —
the default run only shows you the plan:

```bash
organized-docs ~/Documents/"Unsorted Raw Docs"            # dry run: shows the plan
organized-docs ~/Documents/"Unsorted Raw Docs" --apply    # actually files everything
```

Every run writes `ORGANIZING-LOG.md` to the destination root: what was filed and
where, what the tool was unsure about, and anything it could not read. Documents it
is not confident about go to `_Needs Review/` under their original names rather than
being guessed at — including scanned PDFs with no text layer, which this version
cannot read.

Filed documents are renamed `YYYY-MM-DD_Sender_Description.ext`. An existing file is
never overwritten; a name collision gets a `-1`, `-2` suffix instead.

| Flag | Default | What it does |
|---|---|---|
| `--apply` | off | Execute the plan. Without it, nothing is created, moved, or deleted. |
| `--copy` | move | Copy instead of move; originals stay where they are. |
| `--output` | `~/Documents/Organized Documents` | Destination root. |
| `--config` | built-in | Your own `taxonomy.json` of folders and keywords. |
| `--threshold` | `0.6` | How confident the tool must be to file a document (0–1). |
| `--report` | `ORGANIZING-LOG.md` | Where the log goes. |
| `--flat` | recursive | Only scan the top level of the source folder. |

It reads and writes local disk only — no network, no accounts, no uploads. That is
enforced by a test that blocks all sockets and runs the whole pipeline.

---

## Who this is for
- Pro se litigants managing court documents
- Families with paperwork chaos
- Anyone who needs a system but can't afford one
- People who want their files private and local — no cloud, no subscriptions

---

## Coming soon
- OCR for scanned documents, so image-only PDFs stop landing in `_Needs Review/`
- Gmail label sync
- "I'm Dead Now What" document generator
- Full Legal Mail Chrome extension

---

*Built in Texas. For people who fight back.*
