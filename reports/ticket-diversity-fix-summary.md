# Ticket diversification and Ticket Builder repair

Each match is allocated to one active ticket across standard, high-probability, cross-market, high-odds, sure-odds and weekly-draw builders. Alternate markets on the same match share that allocation. Refreshes preserve existing allocations; concurrent inserts and booking execution check the same policy. When the unused pool is insufficient, fewer tickets are produced.

Removed synthetic combined tickets that repeated existing legs. Restored the shared Panel, Badge and Prob components, fixing the Ticket Builder runtime crash.

The live reconciliation retired eight overlapping recommendations. Original legs, booking codes and evidence remain in history. Three tickets with upcoming matches remain active; the audit found zero repeated matches by either fixture ID or normalized matchup.

Validation: 283 backend tests passed (one skipped); 10 booking tests passed again after the final execution guard. Production build passed. Browser checks verified Ticket Builder and code history render without JavaScript errors. API and worker restarted through the existing development supervisor.

See ticket-diversity-repair.json, ticket-diversity-full-tests.log and ticket-builder-browser-check.json for results.
