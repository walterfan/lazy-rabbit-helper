#!/bin/bash -e
date=$(date +%Y%m%d)
log_file="./logs/sticky_note_$date.log"
mkdir -p ./logs
./src/lazy_rabbit_helper/main.py -f ./etc/sticky_note.yaml -t blog
