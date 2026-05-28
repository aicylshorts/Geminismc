#!/bin/bash
echo "Initializing SMC Engine Environment..."
pip install -r requirements.txt
export PYTHON_UNBUFFERED=1
python main.py

