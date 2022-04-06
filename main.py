import json
import os.path
import socket
import time
from datetime import datetime, timedelta
from pathlib import Path
import subprocess
from typing import List

from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
from google_quickstart import get_service
import sys
from config import Config

START_DATE = datetime(year=2022, month=4, day=3)
DT_FORMAT = '%Y-%m-%d %H:%M'


class Lock:
    def __init__(self, name: str):
        self.name = name
        self.acquired = True

    @staticmethod
    def lockfile(name: str):
        return Path(Path.home(), f"{name}.lock")

    @classmethod
    def acquire(cls, name: str):
        try:
            cls.lockfile(name).touch(exist_ok=False)
            return cls(name)
        except FileExistsError:
            return None

    def release(self):
        self.lockfile(self.name).unlink(missing_ok=False)
        self.acquired = False


class BreakReminder:
    def __init__(self, name: str, check_interval: int = 30):
        self.name = name

        self.config = Config(self.name)

        assert check_interval <= self.config.break_interval/2
        self.check_interval = check_interval

        self.last_uploaded_time = 0
        self.service = None
        self.raised_reminders = set()

    def loop(self):
        lock = Lock.acquire(self.name)

        if lock:
            try:
                self._loop_unsafe()
            finally:
                lock.release()

    def _loop_unsafe(self):
        while True:
            self.sleep_until_break()

            if (time.time() - self.last_uploaded_time) > self.config.break_interval:
                self.service = get_service()
                self.download(self.current_file())
                self.reckon_reminders()

            activity = self.activity_prompt()
            raised = self.raise_reminders()
            completed = self.reminder_prompt()

            self.log_activity(activity, raised, completed)

            self.look_away_reminder()
            look_away_start = time.time()

            self.upload(self.current_file())
            self.last_uploaded_time = time.time()
            time.sleep(look_away_start + self.config.look_away_time - time.time())

            self.look_away_end()

    @staticmethod
    def activity_prompt():
        return subprocess.run([
            "zenity",
            "--entry",
            "--text=What are you up to?",
            "--title=break reminder",
        ], stdout=subprocess.PIPE, text=True).stdout.strip()

    @classmethod
    def current_file(cls):
        return cls.date_file(datetime.utcnow())

    @staticmethod
    def date_file(dt: datetime):
        folderpath = Path(f"Activity/{dt.year}/{dt.month}/{dt.day}")
        folderpath.mkdir(parents=True, exist_ok=True)

        return str(Path(folderpath, "activity.json"))

    def get_today(self):
        with open(self.current_file(), 'r') as fh:
            contents = json.load(fh)

        return contents

    def reckon_reminders(self):
        contents = self.get_today()

        self.raised_reminders = set()

        for item in contents['activity']:
            for raised in item['raised']:
                self.raised_reminders.add(raised)

            for completed in item['completed']:
                self.raised_reminders.remove(completed)

    def raise_reminders(self):
        raised = set()

        for reminder, times in self.config.reminders.items():
            if reminder in self.raised_reminders:
                continue

            now = datetime.now()
            last_checked = datetime.strptime(self.get_today()['activity'][-1]['time'], DT_FORMAT)

            for time_string in times:
                hours, minutes = time_string.split(':')

                today_time = datetime(
                    year=now.year,
                    month=now.month,
                    day=now.day,
                    hour=int(hours),
                    minute=int(minutes),
                )

                if last_checked < today_time < now:
                    raised.add(reminder)

        self.raised_reminders = self.raised_reminders | raised

        return list(raised)

    def reminder_prompt(self):
        reminders = list(self.raised_reminders)

        if len(reminders) == 0:
            return []

        results = subprocess.run([
            "zenity",
            "--forms",
            "--text=Which tasks have been completed? Type 'done' to indicate completion.",
            "--title=break reminder",
            *[
                f"--add-entry={reminder}"
                for reminder in reminders
            ],
        ], stdout=subprocess.PIPE, text=True).stdout.strip().split('|')

        completed = [
            reminders[i]
            for i in range(len(reminders))
            if results[i].lower() == 'done'
        ]

        for reminder in completed:
            self.raised_reminders.remove(reminder)

        return completed

    def log_activity(self, activity: str, raised: List[str], completed: List[str]):
        dt = datetime.utcnow()
        contents = self.get_today()

        contents['activity'].append({
            'time': dt.strftime(DT_FORMAT),
            'activity': activity,
            'device': f"{os.getlogin()}@{socket.gethostname()}",
            'raised': raised,
            'completed': completed,
        })

        with open(self.current_file(), 'w') as fh:
            json.dump(contents, fh, indent=2, sort_keys=True)

    def look_away_reminder(self):
        subprocess.run([
            "zenity",
            "--info",
            f"--text=Look away from your screen for {self.config.look_away_time} seconds.",
            "--title=break reminder",
        ])

    @staticmethod
    def look_away_end():
        subprocess.run([
            "ogg123",
            os.path.join("bloop.ogg"),
        ])

    def sleep_until_break(self):
        while (-time.time() % self.config.break_interval) >= self.check_interval:
            time.sleep(-time.time() % self.check_interval)

        time.sleep(self.check_interval)

    def get_folder_id(self, folders):
        parents = []

        for i, folder in enumerate(folders):
            query = f" name = '{folder}'"
            if parents:
                query += f" and '{parents[0]}' in parents"

            files = self.service.files().list(
                q=query,
            ).execute()['files']

            if files:
                file = files[0]
            else:
                file_metadata = {
                    'parents': parents,
                    'name': folder,
                    'mimeType': 'application/vnd.google-apps.folder',
                }

                file = self.service.files().create(
                    body=file_metadata,
                    fields='id',
                ).execute()

            parents = [file.get('id')]

        return parents[0]

    def get_file(self, filepath: str):
        *folders, filename = Path(filepath).parts
        parent_id = self.get_folder_id(folders)

        query = f"name = '{filename}' and '{parent_id}' in parents"
        files = self.service.files().list(
            q=query,
        ).execute()['files']

        if files:
            return files[0]

        else:
            file_metadata = {
                'parents': [parent_id],
                'name': filename,
            }

            return self.service.files().create(
                body=file_metadata,
                fields='id',
            ).execute()

    def upload(self, filepath: str):
        file = self.get_file(filepath)
        media = MediaFileUpload(filepath, mimetype='text/plain')

        self.service.files().update(
            fileId=file['id'],
            media_body=media,
        ).execute()

    def download(self, filepath: str):
        file = self.get_file(filepath)
        print(file)

        with open(filepath, 'wb') as fh:
            request = self.service.files().get_media(fileId=file['id'])

            downloader = MediaIoBaseDownload(fh, request)
            done = False

            while not done:
                status, done = downloader.next_chunk()

    def upload_all(self):
        self.service = get_service()

        date = START_DATE

        while date < datetime.utcnow():
            filepath = self.date_file(date)

            if os.path.exists(filepath):
                self.upload(filepath)

            date += timedelta(days=1)

    def download_all(self):
        self.service = get_service()

        date = START_DATE

        while date < datetime.utcnow():
            self.download(self.date_file(date))

            date += timedelta(days=1)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Please provide an argument")
        sys.exit(1)

    br = BreakReminder("br")

    command = sys.argv[1]

    if command == "run":
        br.loop()
    elif command == "upload":
        br.upload_all()
    elif command == "download":
        br.download_all()
