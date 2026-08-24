"""Automated weekday sentiment digest schedule for EURUSD."""

from managed_deepagents import define_schedule

schedule = define_schedule(
    cron="0 8 * * 1-5",
    timezone="Etc/GMT",
    prompt="Fetch the latest EURUSD retail sentiment from Myfxbook and output the current sentiment report.",
)
