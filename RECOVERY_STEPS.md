# Recovery Steps - Fixing 225 Image Cards

## Overview

You need to register 225 existing images with Telegram. This takes about 5 minutes total.

---

## Step 1: SSH into Your Fly Machine

Open your terminal and run:

```bash
fly ssh console -a srs-bot-shapka
```

You should see something like:

```
Connecting to fdaa:... complete
root@...:/app#
```

You're now inside the Fly container at `/app`.

---

## Step 2: Verify the Problem

Check how many cards need fixing:

```bash
python3 << 'EOF'
import sqlite3
c = sqlite3.connect("/data/srs.db")
need = c.execute("""
  SELECT COUNT(*) FROM card
  WHERE deleted_at IS NULL
    AND back_image_sha256 IS NOT NULL
    AND back_image_file_id IS NULL
""").fetchone()[0]
print(f"Cards needing reupload: {need}")
c.close()
EOF
```

**Expected output:** `Cards needing reupload: 225`

---

## Step 3: Run Dry Run (Preview Only)

See what will be uploaded without making changes:

```bash
python -m scripts.reupload_images --dry-run
```

**Expected output:**
```
would reupload 225 images (front=0, back=225)
  card_id=2 side=back sha=8c5c799b4d8b...
  card_id=3 side=back sha=822f387c1d9b...
  ...
```

This shows it will process 225 back-side images. Press `Ctrl+C` if you see any errors.

---

## Step 4: Run the Fix (Takes ~3-5 Minutes)

Upload all images and register file_ids:

```bash
python -m scripts.reupload_images
```

**What happens:**
- The script reads each image from `/data/images/<sha256>.jpg`
- Uploads it to Telegram via `bot.send_photo()`
- Telegram returns a `file_id`
- Script saves `file_id` back to the database
- You'll see 225 photo messages in your Telegram chat (this is normal!)

**Expected output:**
```
reuploading card_id=2 side=back sha=8c5c799b... ✓
reuploading card_id=3 side=back sha=822f387c... ✓
...
reuploaded 225 images (front=0, back=225)
```

Takes about 2-4 minutes depending on network speed.

---

## Step 5: Verify the Fix

Check that all images are now registered:

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
print(f"✓ Cards with images registered: {fixed}")
c.close()
EOF
```

**Expected output:** `✓ Cards with images registered: 225`

---

## Step 6: Test in Telegram

Exit the SSH session:

```bash
exit
```

In your Telegram bot, run:

```
/review
```

Now when you tap "Show answer", cards with images should display the photo!

---

## Step 7: Deploy Updated Code

Back on your local machine, deploy the updated bulk import code so future imports work correctly:

```bash
# Already pushed to GitHub
# Now deploy to Fly
fly deploy -a srs-bot-shapka
```

**Expected output:**
```
==> Building image
...
==> Pushing image to fly
...
--> Done
```

Takes 2-3 minutes to build and deploy.

---

## Done!

✅ Existing 225 cards now show images  
✅ Future CSV imports will register images automatically  
✅ Bot is running the latest code

---

## Troubleshooting

### Issue: "No such file: /data/srs.db"

Check the DB path:

```bash
ls -la /data/srs.db
```

Should show: `-rw-r--r-- 1 root root ... /data/srs.db`

### Issue: "Module scripts.reupload_images not found"

You're not in the `/app` directory:

```bash
cd /app
python -m scripts.reupload_images --dry-run
```

### Issue: "Bot token error"

The script reads `BOT_TOKEN` from environment. Verify it's set:

```bash
env | grep BOT_TOKEN | head -c 20
```

Should print the first 20 chars of your token.

### Issue: Script crashes mid-run

That's fine! The script processes one card at a time. Just run it again:

```bash
python -m scripts.reupload_images
```

It will skip cards already fixed and resume from where it crashed.

---

## Quick Reference

| Command | Purpose |
|---------|---------|
| `fly ssh console -a srs-bot-shapka` | SSH into Fly |
| `python -m scripts.reupload_images --dry-run` | Preview changes |
| `python -m scripts.reupload_images` | Fix images |
| `fly deploy -a srs-bot-shapka` | Deploy updated code |
| `exit` | Leave SSH session |
