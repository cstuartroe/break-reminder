import toml
import os
from pathlib import Path


class Config:
    def __init__(self, name: str):
        self.name = name
        self.config_file = str(Path(Path.home(), f"{name}.config.toml"))

        if os.path.exists(self.config_file):
            with open(self.config_file, 'r') as fh:
                self.config = toml.load(fh)

        else:
            self.config = {}

        self.added_key = False

        self.break_interval = self.get('break_interval', 15)*60
        self.look_away_time = self.get('look_away_time', 60)
        self.reminders = self.get('reminders', {
            "Drink water": ["10:00"],
        })

        if self.added_key:
            with open(self.config_file, 'w') as fh:
                toml.dump(self.config, fh)

    def get(self, key, default=None):
        if key not in self.config:
            self.added_key = True
            self.config[key] = default

        return self.config[key]
