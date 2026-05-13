# 24/7 cloud deploy — step by step

You have two paths. Pick one.

| | Docker (recommended) | systemd |
|---|---|---|
| Setup | one script | manual |
| Updates | `docker compose up -d --build` | pull + restart services |
| Logs | `docker compose logs -f` | `journalctl -u research-scheduler` |
| Where Python lives | inside container | on host |

---

## Recommended cheap VPS providers (May 2026)

| Provider | Plan | ₹/month-ish | Specs |
|---|---|---|---|
| Hetzner Cloud (Falkenstein DE) | CX11 | ~₹375 | 1 vCPU / 2 GB |
| DigitalOcean (BLR1, Bangalore) | basic | ~₹500 | 1 vCPU / 1 GB |
| Linode (Mumbai) | Nanode 1GB | ~₹500 | 1 vCPU / 1 GB |
| AWS Lightsail (Mumbai) | $3.50 | ~₹300 | 0.5 GB |
| Vultr (Mumbai) | Cloud Compute | ~₹500 | 1 vCPU / 1 GB |

Hetzner is cheapest. DigitalOcean / AWS Lightsail / Linode all have **Bangalore or Mumbai regions** which means lower latency to NSE/BSE/Yahoo for the fetchers.

Either way: **1 GB RAM is enough**, the system is light.

---

## Path A — Docker (fastest, recommended)

### One-line install on a fresh Ubuntu 22.04/24.04 VPS

```bash
ssh root@your.vps.ip                 # or ssh user@vps if not root
adduser monitor && usermod -aG sudo monitor    # create non-root user
su - monitor

git clone https://github.com/<your-github-user>/dashboard.git
cd dashboard
git checkout claude/indian-equity-research-system-ZGTuG
bash research_system/deploy/install.sh
```

The script will:
1. install Docker + Compose plugin
2. ask for your `ANTHROPIC_API_KEY`, Telegram token + chat ID
3. write `.env` (mode 600)
4. build the image
5. start three containers: `research-scheduler`, `research-telegram`, `research-dashboard`
6. print the SSH-tunnel command for your laptop

### Daily commands

```bash
cd ~/dashboard
COMPOSE="docker compose -f research_system/deploy/docker-compose.yml --env-file .env"

$COMPOSE ps                         # who's up
$COMPOSE logs -f scheduler          # tail scheduler
$COMPOSE logs -f telegram           # tail bot
$COMPOSE restart                    # restart all
$COMPOSE down                       # stop all
$COMPOSE up -d --build              # pull new code, rebuild, restart
```

### View the dashboard from your phone/laptop (no public exposure)

The container binds **127.0.0.1:8501** on the VPS — not reachable from the
internet. Forward it over SSH from your laptop:

```bash
ssh -N -L 8501:127.0.0.1:8501 monitor@your.vps.ip
# now open http://localhost:8501 in your laptop browser
```

To view from your phone, use the **Termius** app or **JuiceSSH** with port
forwarding — both have free tiers and walk you through the tunnel setup.

### Updating after you change config / theses

```bash
ssh monitor@your.vps.ip
cd ~/dashboard
git pull
docker compose -f research_system/deploy/docker-compose.yml \
  --env-file .env up -d --build
```

---

## Path B — systemd (no Docker)

If you'd rather run Python directly on the host:

```bash
# As root or with sudo:
apt-get update && apt-get install -y python3.11 python3.11-venv git
adduser monitor && usermod -aG sudo monitor
su - monitor

git clone https://github.com/<your-github-user>/dashboard.git
cd dashboard
git checkout claude/indian-equity-research-system-ZGTuG

cd research_system
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
nano .env                                 # paste your secrets
.venv/bin/python -m research_system.db    # init DB
cd ..
```

Install the systemd units (edit `User=` and `WorkingDirectory=` first if your
username/path is not the default `ubuntu` / `/home/ubuntu/dashboard`):

```bash
sudo cp research_system/deploy/systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now research-scheduler research-telegram research-dashboard
```

Check:

```bash
systemctl status research-scheduler
journalctl -u research-scheduler -f
journalctl -u research-telegram -f
```

Update flow:

```bash
cd ~/dashboard && git pull
sudo systemctl restart research-scheduler research-telegram research-dashboard
```

---

## Security tips for both paths

1. **Don't expose Streamlit publicly.** No login wall. Always SSH-tunnel.
2. **Lock SSH** to key-only: `sudo nano /etc/ssh/sshd_config` → `PasswordAuthentication no`.
3. **Firewall**: `ufw allow OpenSSH && ufw enable`. Nothing else needs to be open.
4. **Backup the DB** weekly: it's just one file.
   ```bash
   rsync your.vps.ip:~/dashboard/data/research.db ~/research-backups/
   # or with Docker volumes:
   docker run --rm -v research_research-data:/d -v $(pwd):/b alpine \
       tar czf /b/research-data-$(date +%F).tgz -C /d .
   ```
5. **`.env` is mode 600** by the installer — keep it that way.

---

## Cost summary

- Hetzner CX11 (cheapest reliable) + Anthropic API spend ~₹500–1500/month
  depending on how chatty the fetchers are.
- Bandwidth: trivial (<100 MB/month).
- Storage: SQLite stays under 200 MB even with a year of news.

---

## If something breaks

| Symptom | Where to look |
|---|---|
| Bot doesn't reply | `docker compose logs telegram` or `journalctl -u research-telegram` — check token / chat-id |
| Nothing new in feed | `docker compose logs scheduler` — fetchers may be 403'd, especially NSE; consider switching VPS region to Bangalore/Mumbai |
| Claude calls failing | check `ANTHROPIC_API_KEY` in `.env`, then check Anthropic console for spend limits |
| Dashboard 502 / no connect | SSH tunnel still alive? `ss -ltn` on VPS — confirm port 8501 listening on 127.0.0.1 |
| Disk fills up | `journalctl --vacuum-time=30d` or rotate `/var/log/research-*.log` |
