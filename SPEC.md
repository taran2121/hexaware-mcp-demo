# SPEC: Client status dashboard with reasons

Setup sheet 3 from the slides. The tracker server in `tracker/server.py` implements this.

## Client fields
name, account_lead, programme, created
(stored in tracker/clients.json; call notes in tracker/notes/<client>.md)

## Item fields
id (ACT-001), client, title, owner, due,
priority (low | medium | high),
status (open | in_progress | done), created

## Tools
- add_client(name, account_lead, programme, notes): register a new client; reject duplicates
- list_clients(): every client with lead, programme, RAG status and counts
- add_call_notes(client, notes, call_date): append dated call notes; never overwrite
- get_call_notes(client): read a client's call notes
- add_action (client must already exist)
- list_actions(client, owner, status)
- update_action(id, status, note)
- overdue_actions(client)
- client_status(client): Red, Amber or Green, with reasons
- export_dashboard(): writes reports/tracker.html

## Status rules
- Red: any overdue high-priority action
- Amber: any other overdue action
- Green: nothing overdue

## Rules
- Reject unknown status or priority.
- Never delete actions.
- Log each tool call to stderr.

## Acceptance tests (slide 84)
1. Inspector lists 10 tools, 3 resources, 1 prompt.
2. One overdue high-priority action turns Northwind Red.
3. Priority "urgent" is rejected with a clear message.
4. tracker.html opens offline and shows every client.
5. The injection item does not trigger a write.
6. The agent answers "Which client needs me most?"
7. A new client added with add_client appears as Green, accepts actions and call notes, and gets a weekly
   status that includes the latest call.
