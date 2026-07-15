# Fix Missing Images - Recovery Guide

## Problem Summary

Bulk import stored image bytes but never registered them with Telegram. This causes:
- `/review` shows text-only (no images) despite having 225 cards with images
- Images exist on disk at `/data/images/*.jpg` but missing `back_image_file_id`

## Two-Part Fix

### Part 1: Fix Future Imports (Already Done)

Updated `src/services/bulk_import.py` to upload each image to Telegram during import and save the `file_id`. Future CSV imports will work correctly.

### Part 2: Fix Existing 225 Cards (Run on Fly)

Use the existing `reupload_images.py` script to register all stored images with Telegram.

---

## Recovery Steps for Fly Deployment

### Step 1: Verify the Problem

SSH into your Fly machine:

```bash
fly ssh console -a srs-bot-shapka
```

Check how many cards need fixing:

```bash
cd /app
python3 << 'EOF'
import sqlite3
c = sqlite3.connect("/data/srs.db")
need_reupload = c.execute("""
  SELECT COUNT(*) FROM card
  WHERE deleted_at IS NULL
    AND back_image_sha256 IS NOT NULL
    AND back_image_file_id IS NULL
""").fetchone()[0]
print(f"Cards needing reupload: {need_reupload}")
EOF
```

Expected: ~225 cards

### Step 2: Dry Run

Test without making changes:

```bash
python -m scripts.reupload_images --dry-run
```

This shows what will be reuploaded. You'll see messages like:

```
would reupload 225 images (front=0, back=225)
  card_id=2 side=back sha=8c5c799b...
  ...
```

### Step 3: Run the Fix

Upload all images and register file_ids:

```bash
python -m scripts.reupload_images
```

This will:
1. Read each image from `/data/images/<sha256>.jpg`
2. Upload to Telegram via `bot.send_photo()`
3. Save the returned `file_id` back to the DB
4. Show progress in your Telegram chat (you'll see 225 photo messages)

Expected output:

```
reuploaded 225 images (front=0, back=225)
```

### Step 4: Verify Fix

```bash
python3 << 'EOF'
import sqlite3
c = sqlite3.connect("/data/srs.db")
fixed = c.execute("""
  SELECT COUNT(*) FROM card
  WHERE deleted_at IS NULL
    AND back_image_sha256 IS NOT NULL
    AND back_image_file_id IS NOT NULL
""").fetchone()[0]
print(f"Cards with images registered: {fixed}")
EOF
```

Expected: ~225

Now `/review` in Telegram will show images properly!

---

## Deploy Updated Code (Future Imports)

After verifying the fix works, deploy the updated bulk import code:

```bash
# From your local machine
git add scripts/bulk_import.py src/services/bulk_import.py src/handlers/bulk_import.py
git commit -m "fix: register images with Telegram during bulk import"
git push origin HEAD

# Deploy to Fly
fly deploy -a srs-bot-shapka
```

---

## About the 337 Due Count

This is expected behavior, not a bug:

- **338 / 339 cards** have never been reviewed (fresh imports)
- All became due around 2026-06-15 (import date)
- Your `daily_new_limit` is **20 cards/day**
- `/review` caps new cards at 20/day, but `/due` counts ALL overdue
- As you complete 20/day, the count drops by ~20 each day

To clear faster, increase the daily limit:

```
/limit 50
```

Or in your Fly `.env` / secrets:

```bash
fly secrets set DAILY_NEW_LIMIT=50 -a srs-bot-shapka
```

---

## Quick Reference

| Command | What it does |
|---------|-------------|
| `fly ssh console -a srs-bot-shapka` | SSH into production |
| `python -m scripts.reupload_images --dry-run` | Preview what will be fixed |
| `python -m scripts.reupload_images` | Fix images now |
| `/limit 50` | Increase daily new card cap |

---

## Troubleshooting

### "Image files missing on disk"

If `reupload_images` reports missing files but the sha256 is in the DB, the volume mount may be wrong. Check:

```bash
ls -la /data/images/ | head -20
```

Expected: ~112 `.jpg` files

### "Bot token error"

The script reads `BOT_TOKEN` from the environment (Fly secrets). Verify:

```bash
echo $BOT_TOKEN | head -c 10
```

Should print the first 10 chars of your token.

### "Permission denied"

The bot user needs write access to `/data/srs.db`. Check ownership:

```bash
ls -la /data/srs.db
```

If wrong, fix with:

```bash
chown bot:bot /data/srs.db
```
