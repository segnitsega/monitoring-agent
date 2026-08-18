"""Metric and backup-evidence collectors.

``health`` gathers host metrics via psutil; ``backup`` provides pluggable
checkers that read evidence of the server's existing backup jobs.
"""
