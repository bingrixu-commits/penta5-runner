#!/bin/bash
while true; do
    cd /root/penta5-runner
    python3 auto_r1_killer.py >> auto_r1.log 2>&1
    python3 auto_r1_framework.py >> auto_r1_framework.log 2>&1
    sleep 5
done
