# Deployment Guide — Motivated Seller Digest (Level 1)

Total cost to launch: domain (~£10/yr) + £0/month. Time: ~1 hour.

## Files

| File | Purpose | Goes where |
|---|---|---|
| `motivated_seller_finder.py` | Pulls & scores listings | Repo root |
| `send_digest.py` | Emails the digest | Repo root |
| `weekly-digest.yml` | Weekly automation | `.github/workflows/weekly-digest.yml` |
| `deal_alerts_landing.html` | Sales page | Netlify / Cloudflare Pages (rename `index.html`) |
| `subscribers.txt` | One subscriber email per line | Repo root (create it) |

## Step 1 — Accounts (all free tiers)

1. **Homedata** — homedata.co.uk/register → copy API key (100 calls/month free).
2. **Resend** — resend.com → add and verify your domain (they give you 3 DNS records), copy API key. Until the domain verifies you can send from `onboarding@resend.dev` for testing.
3. **Stripe** — stripe.com → Products → Add product ("DealSignal Bradford", £79/month recurring) → create a **Payment Link** → copy the `buy.stripe.com/...` URL.
4. **GitHub** — you'll need a free account.
5. **Netlify** or **Cloudflare Pages** — free account for the landing page.

## Step 2 — Repo setup

```bash
# New PRIVATE GitHub repo, then locally:
git clone https://github.com/YOU/dealsignal.git
cd dealsignal
# copy in: motivated_seller_finder.py, send_digest.py
mkdir -p .github/workflows
# copy weekly-digest.yml into .github/workflows/
echo "# one email per line" > subscribers.txt
echo "your.own.email@example.com" >> subscribers.txt   # yourself, for testing
git add -A && git commit -m "initial" && git push
```

## Step 3 — Secrets & variables

Repo → **Settings → Secrets and variables → Actions**:

Secrets (required):
- `HOMEDATA_API_KEY` — from step 1.1
- `RESEND_API_KEY` — from step 1.2
- `FROM_EMAIL` — e.g. `Deal Alerts <alerts@yourdomain.co.uk>`

Variables (optional):
- `AREA_NAME` — default `Bradford`
- `MAX_PRICE` — default `250000`

## Step 4 — Test run

Repo → **Actions** tab → "Weekly motivated-seller digest" → **Run workflow**.

- Check the log: the finder step prints listings found and API calls used.
- Check your inbox for the digest.
- Common first-run fixes: field-name mismatches from the live API (paste the error to Claude), or Resend rejecting the sender until the domain verifies.

Once green, it runs itself every Monday 07:00 UTC. Cron isn't guaranteed to the minute on free GitHub — fine for a weekly digest.

## Step 5 — Landing page

1. Edit `deal_alerts_landing.html`: replace **both** `https://buy.stripe.com/YOUR_PAYMENT_LINK` occurrences with your real link, and `hello@YOURDOMAIN.co.uk` with your email.
2. Rename to `index.html`.
3. Netlify: drag-and-drop the file at app.netlify.com/drop — live in 30 seconds. Then Site settings → Domain management → add your custom domain (point DNS as instructed).
   (Cloudflare Pages: create project → direct upload → same idea.)

## Step 6 — Wire up new subscribers

When Stripe emails you "New subscription":

1. Add the customer's email to `subscribers.txt`, commit, push. Done — they get Monday's digest.
2. When someone cancels (Stripe emails you), remove the line.

Manual on purpose: at Level 1 volume this is 30 seconds per subscriber. Automate it (Stripe webhook → GitHub API) only when it hurts.

## Step 7 — First subscribers

- Send the sample digest to 10–20 active investors: local property Facebook groups, PIN (Property Investors Network) meetup attendees, property sourcer communities.
- Post one deal (partially redacted) weekly in those groups with "full list is £79/mo" — the product demos itself.
- The refund guarantee on the landing page does the rest.

## Watch-outs

- **API quota**: default run ≈ 10 calls (5 search + 5×1 deep dives); 4 runs/month ≈ 40 — comfortably within the free 100. Increasing `--deep-dive` to 10 would use ~60/month. Take Homedata's Starter plan only when scaling to many areas.
- **Homedata ToS**: you're selling derived scores and analysis, not raw data redistribution — but confirm with them before scaling (they have industry packs for property sourcers, so this use case is clearly on their radar).
- **Fresh area = fresh product**: to add Leeds, duplicate the workflow file with different `AREA_NAME`/`MAX_PRICE` variables and a second subscribers file. One repo can serve many areas.

## Upgrade path (Level 2, later)

When you pass ~5 subscribers: FastAPI app on Render reading archived CSVs from the repo (the workflow already stores 90 days of results as artifacts), Stripe Checkout + webhook for self-serve signup, magic-link login. Ask Claude when you're ready.
