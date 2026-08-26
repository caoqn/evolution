#!/bin/zsh
set -euo pipefail

base_dir='/Users/caoqinuo/Desktop/6月课题/meta_team evolution/Meta-Team'
state_file="$base_dir/runs/gaia32_retest_monitor.state"
job_label='com.metateam.gaia32retest'
all_cases=(1 5 10 14 40 47 51 54 56 61 63 64 65 66 67 68 69 70 71 72 73 74 75 76 78 86 88 89 90 92 93 94)
uid="$(id -u)"
log_file='/tmp/gaia32_retest_supervisor.log'

if [[ -f "$state_file" ]]; then
  source "$state_file"
else
  run_dir="$base_dir/runs/20260814_gaia_retest_api18_timeout14_t3000_asyncio_w6_launchd"
fi

completed=0
pending=()
for i in {1..32}; do
  local_index=$((i - 1))
  case_dir=($run_dir/cases/$(printf '%03d' "$local_index")_*(N))
  if [[ ${#case_dir[@]} -eq 1 && -f "$case_dir[1]/result.json" ]]; then
    ((completed += 1))
  else
    pending+=("${all_cases[$i]}")
  fi
done

if [[ "$completed" -eq 32 ]]; then
  print "$(date '+%F %T') completed 32/32: supervisor idle" >> "$log_file"
  exit 0
fi

if launchctl print "gui/$uid/$job_label" 2>/dev/null | grep -q 'state = running'; then
  print "$(date '+%F %T') running: $completed/32 completed" >> "$log_file"
  exit 0
fi

new_id="$(date '+%Y%m%d_%H%M%S')_gaia32_pending_restart"
new_dir="$base_dir/runs/$new_id"
case_list="${(j:,:)pending}"
command="cd '$base_dir' && exec env PYTHONUNBUFFERED=1 .venv311/bin/python benchmarks/adapter_gaia.py --split test_100 --cases '$case_list' --team pool_GAIA_MT_v019_frozen --timeout 3000 --workers 6 --run-id '$new_id' >> '/tmp/$new_id.log' 2>&1"

launchctl bootout "gui/$uid/$job_label" 2>/dev/null || true
launchctl submit -l "$job_label" -- /bin/zsh -lc "$command"
print -r -- "run_dir='$new_dir'" > "$state_file"
print "$(date '+%F %T') restarted ${#pending[@]} pending cases in $new_id" >> "$log_file"
