#!/usr/bin/env python3
"""Diagnose monitoring without connecting to Telegram."""
import os
from dotenv import load_dotenv
from app.config import load_config
from app.formatting import full_status
from app.probe import probe

if __name__ == '__main__':
    load_dotenv()
    config = load_config(os.getenv('CONFIG_FILE', 'config.yaml'))
    for server in config.servers:
        result = probe(server)
        print(full_status(server, result), end='\n\n' + '-' * 50 + '\n\n')
