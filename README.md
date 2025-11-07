# chore queue (todoist) — fully automated

this script keeps a rolling queue in todoist so only the **next** chore is due **today**; the rest have no date. miss a day? nothing piles up.

## setup
1. make a project in todoist for your chore queue.
2. create tasks **in order**. best practice: prefix with numbers for stable order:
   - `01 take out trash`
   - `02 wipe counters`
   - `03 tidy toys`
3. install deps:
   ```bash
   python3 -m pip install -r requirements.txt
   ```
4. create a `.env` file in this directory:
   ```bash
   cp .env.example .env
   ```
5. edit `.env` with your settings:
   - get your todoist token (settings → integrations)
   - set your project name
   ```
   TODOIST_TOKEN=your_api_token_here
   PROJECT_NAME=chore queue
   ```
6. run:
   ```bash
   python3 chore_queue.py
   ```

## schedule it (cron on mac/linux)
edit crontab:
```bash
crontab -e
```
run it every morning at 6:10:
```
10 6 * * * cd /path/to/TodoistChoreQueue && python3 chore_queue.py >> chore_queue.log 2>&1
```
(the script will load the .env file automatically)

## multiple queues
open `chore_queue.py` and duplicate the dict in `QUEUES` with a new `project_name` (e.g. `"kitchen queue"`). each queue gets its head task promoted independently.

## how it works
- finds the **lowest** numbered incomplete task and sets `due_string="today"`
- clears due dates on the others so they never backlog
- optionally tags the head with `@next` (auto‑creates label) — toggle with `promote_label`

## faq
- **do i need numeric prefixes?** recommended. if you don't, tasks fall back to creation order, which can shift when you edit things.
- **what happens if i complete the head during the day?** rerun the script (or let cron hit next morning) and the new head becomes due today.
- **can i make the due time 6pm?** set `"due_string": "today 6pm"` in the queue config.
- **time zone?** todoist applies the due_string in your account tz.
