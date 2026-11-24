# Put Kodisha on Railway (playground)

Goal: a private test copy you and your team can open from anywhere, with fake data, for about $5 to $10 a month.
Do not put real tenant data here yet.

## What you need
- A GitHub account (free) and a Railway account on the Hobby plan.
- The `rentals` folder from the zip.

## Steps
1. **Put the code on GitHub.** Create a new *private* repository called `kodisha`. Upload the contents of the `rentals` folder
   (the folder that contains `manage.py`) to it. Do not upload `db.sqlite3` or `.venv`.
2. **Create the Railway project.** In Railway choose New Project, then Deploy from GitHub repo, and pick `kodisha`.
   The first build may fail until the variables below are set. That is normal.
3. **Add the database.** In the project choose New, then Database, then PostgreSQL.
4. **Set variables** on the `kodisha` service (Variables tab):

   | Name | Value |
   |---|---|
   | `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` (Railway fills this in from the database) |
   | `DJANGO_SECRET_KEY` | a long random string, 50 or more characters |
   | `KODISHA_SANDBOX` | `1` (shows the Simulate M-Pesa button) |
   | `ADMIN_USERNAME` | your login name |
   | `ADMIN_PASSWORD` | a strong password you choose |
   | `SEED_DEMO` | `1` (loads the sample company; set to `0` later if you want an empty system) |

5. **Get a web address.** In the service Settings choose Networking, then Generate Domain.
6. **Redeploy** if the build had failed. Open the address and sign in with `ADMIN_USERNAME` and `ADMIN_PASSWORD`.

## Notes
- Background jobs run inside the web process on the playground (`CELERY_EAGER` defaults to on), so no Redis or worker is needed.
- To update: push changes to GitHub and Railway redeploys. Data in Postgres is kept.
- To start over: delete the database service, add a new one, and redeploy.
- Cost: Hobby is $5 a month including $5 of usage. Check the Usage page after a week.
- Before real use: set `SEED_DEMO=0`, remove `KODISHA_SANDBOX`, choose region and data residency, and add backups.
- First time on PostgreSQL: the ledger's append-only triggers run here for the first time. Tell me and I will check they fire.
