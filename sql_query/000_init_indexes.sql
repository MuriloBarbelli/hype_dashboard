create index if not exists ix_events_access_ts
on public.events (access_name, event_timestamp);

create index if not exists ix_events_ts
on public.events (event_timestamp);
