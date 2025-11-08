#!/usr/bin/env python3
"""
todoist chore queue promoter
- promotes the first incomplete task in each configured project to "due today"
- clears due dates from the rest so nothing piles up
- optional: applies a label like @next to the head task (auto-creates if missing)
requirements: python 3.9+, requests, python-dotenv
usage:
  create .env file with TODOIST_TOKEN and PROJECT_NAME
  python3 chore_queue.py
schedule it with cron/launchd/systemd as you like.
"""

import os
import re
import sys
import json
import time
import typing as t
from datetime import datetime
import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("warning: python-dotenv not found. using environment variables only.", file=sys.stderr)

API_BASE = "https://api.todoist.com/rest/v2"

# ---------- configuration ----------
# load project name from environment variable
PROJECT_NAME = os.environ.get("PROJECT_NAME", "chore queue")

# list the queue projects you want managed
# order is determined by a numeric prefix like "01 task", "02 task", etc.
# if a task has no numeric prefix, it sorts after prefixed items by created date
QUEUES: t.List[dict] = [
    {
        "project_name": PROJECT_NAME,
        "due_string": "today",    # e.g., "today", "today 6pm", "tomorrow 9am"
        "promote_label": "@next", # set None to skip labeling
        "language": "en",         # natural-language parser locale
        "clear_due_on_rest": True # remove due dates from non-head tasks
    },
    # add more projects if desired:
    # {
    #     "project_name": "kitchen queue",
    #     "due_string": "today 6pm",
    #     "promote_label": "@next",
    #     "language": "en"
    # },
]

# ---------- helpers ----------

class Todoist:
    def __init__(self, token: str):
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        })
        self._label_cache = None

    def _req(self, method: str, path: str, **kw):
        url = f"{API_BASE}{path}"
        r = self.session.request(method, url, **kw)
        if r.status_code >= 400:
            raise RuntimeError(f"todoist api error {r.status_code}: {r.text}")
        if r.text and "application/json" in r.headers.get("Content-Type",""):
            return r.json()
        return None

    # projects
    def list_projects(self):
        return self._req("GET", "/projects")

    def get_project_id_by_name(self, name: str) -> t.Optional[int]:
        projects = self.list_projects()
        
        # first try exact match
        for p in projects:
            if p["name"].strip().lower() == name.strip().lower():
                return p["id"]
        
        # if no exact match and name contains '/', try hierarchical lookup
        if '/' in name:
            return self._resolve_hierarchical_project(projects, name)
        
        return None
    
    def _resolve_hierarchical_project(self, projects: list, path: str) -> t.Optional[int]:
        """resolve hierarchical project path like 'Chores/Rotating Chore Queue'"""
        path_parts = [part.strip() for part in path.split('/')]
        
        # build project hierarchy map
        project_map = {}
        children_map = {}
        
        for p in projects:
            project_map[p["id"]] = p
            parent_id = p.get("parent_id")
            if parent_id:
                if parent_id not in children_map:
                    children_map[parent_id] = []
                children_map[parent_id].append(p["id"])
        
        # find root project matching first part
        root_candidates = []
        for p in projects:
            if (not p.get("parent_id") and 
                p["name"].strip().lower() == path_parts[0].lower()):
                root_candidates.append(p["id"])
        
        # traverse path for each root candidate
        for root_id in root_candidates:
            current_id = root_id
            found = True
            
            # traverse remaining path parts
            for part in path_parts[1:]:
                child_ids = children_map.get(current_id, [])
                next_id = None
                
                for child_id in child_ids:
                    child_project = project_map[child_id]
                    if child_project["name"].strip().lower() == part.lower():
                        next_id = child_id
                        break
                
                if next_id is None:
                    found = False
                    break
                    
                current_id = next_id
            
            if found:
                return current_id
        
        return None

    # tasks
    def list_tasks(self, **params):
        return self._req("GET", "/tasks", params=params) or []

    def update_task(self, task_id: int, **fields):
        # todoist uses POST to update
        return self._req("POST", f"/tasks/{task_id}", data=json.dumps(fields))

    # labels
    def ensure_label(self, label_name: str) -> int:
        label_name = label_name.lstrip()
        if not label_name:
            raise ValueError("label name empty")
        labels = self._labels()
        for lab in labels:
            if lab["name"].lower() == label_name.lower():
                return lab["id"]
        # create it
        created = self._req("POST", "/labels", data=json.dumps({"name": label_name}))
        # refresh cache
        self._label_cache = None
        return created["id"]

    def _labels(self):
        if self._label_cache is None:
            self._label_cache = self._req("GET", "/labels") or []
        return self._label_cache

def parse_order_key(task) -> t.Tuple[int, str]:
    """returns a key for sorting: (prefix_int or big, created_at)"""
    content = task.get("content","")
    m = re.match(r"^\s*(\d{2,})\b", content)
    prefix = int(m.group(1)) if m else 10**9
    created = task.get("created_at") or ""
    return (prefix, created)

def promote_queue(todo: Todoist, cfg: dict) -> dict:
    """process one queue. returns summary dict."""
    pname = cfg["project_name"]
    pid = todo.get_project_id_by_name(pname)
    if not pid:
        return {"project": pname, "status": "missing_project"}

    tasks = todo.list_tasks(project_id=pid)  # only active tasks
    if not tasks:
        return {"project": pname, "status": "empty"}

    # filter out checked/completed (api already does), then sort
    tasks_sorted = sorted(tasks, key=parse_order_key)

    head = tasks_sorted[0]
    rest = tasks_sorted[1:]

    # apply due today to head
    due_string = cfg.get("due_string","today")
    language = cfg.get("language","en")
    todo.update_task(head["id"], due_string=due_string, due_lang=language)

    # clear due on rest if requested
    cleared = 0
    if cfg.get("clear_due_on_rest", True):
        for tsk in rest:
            if tsk.get("due") is not None:
                todo.update_task(tsk["id"], due_string="no due date", due_lang=language)
                cleared += 1

    # optional labeling for visibility
    labeled = False
    promote_label = cfg.get("promote_label")
    if promote_label:
        try:
            label_id = todo.ensure_label(promote_label)
            # set labels on head: keep existing + add label if missing
            labels = head.get("labels", [])
            if label_id not in labels and promote_label not in labels:
                # todoist expects label names by id? the REST v2 uses IDs in "labels" field
                new_labels = labels + [label_id]
                todo.update_task(head["id"], labels=new_labels)
            labeled = True
        except Exception as e:
            labeled = False

    return {
        "project": pname,
        "status": "ok",
        "promoted_task": head.get("content","(unnamed)"),
        "cleared_due_on": cleared,
        "labeled": labeled
    }

def main():
    token = os.environ.get("TODOIST_TOKEN")
    if not token:
        print("error: set TODOIST_TOKEN", file=sys.stderr)
        sys.exit(1)

    todo = Todoist(token)
    for cfg in QUEUES:
        try:
            res = promote_queue(todo, cfg)
            print_result(res)
        except Exception as e:
            print(f"error: {cfg.get('project_name','(unknown)')} - {str(e)}")

def print_result(result: dict):
    """print user-friendly status message"""
    project = result["project"]
    status = result["status"]
    
    if status == "ok":
        task = result["promoted_task"]
        cleared = result["cleared_due_on"]
        labeled = result["labeled"]
        
        print(f"success: '{task}' promoted to today in {project}")
        if cleared > 0:
            print(f"  cleared due dates from {cleared} other tasks")
        if labeled:
            print(f"  applied @next label")
            
    elif status == "empty":
        print(f"info: {project} has no tasks")
        
    elif status == "missing_project":
        print(f"error: project '{project}' not found")
        
    elif status == "error":
        error_msg = result.get("error", "unknown error")
        print(f"error: {project} - {error_msg}")

if __name__ == "__main__":
    main()
