"""COE source ingest clients.

Each module (jira, wiz, crowdstrike, vibranium, hr) exposes a single
`fetch_*` async generator. All clients raise subclasses of
`coe.ingest.errors.IngestError`: `AuthError` for 401/403, `TransientError`
after retries exhaust on retriable statuses or transport failures.
"""
