# Manual Meeting Workspace Implementation Plan

> For agentic workers: use superpowers:subagent-driven-development with test-driven-development and independent review. The user approved implementation and integration into the existing local main checkout.

Goal: Add a beautiful, efficient, durable structured meeting workspace without disrupting paste/AI capture.

Architecture: A MeetingSession one-to-one with MeetingNote stores a versioned JSON draft; formal business entities change only at explicit confirmation. Separate services, HTTP views, and page-scoped vanilla JS/CSS.

Tech Stack: Existing Django 5.2 / SQLite / templates / vanilla JS / Docker Compose.

Spec: docs/superpowers/specs/2026-09-06-manual-meeting-workspace.md

## Global Constraints

- Preserve existing user data, env, ignored files and all AI workflows; no external LLM or production fixture writes.
- Server autosave, incomplete drafts, stable UUID items, optimistic version guards, transactional one-time confirmation.
- Existing Task assignee/project are not changed by selecting a reporting person.
- Past meeting task updates are historical-only; future meetings cannot confirm. Reports use occurrence date with legacy fallback.
- Keep dependencies unchanged and use existing UI vocabulary, accessible controls, responsive layout.
- Agents own disjoint files and do not commit, push or launch other agents. Controller handles integration and release.

## Shared contracts

Model `MeetingSession`: `meeting_note` OneToOne CASCADE related_name `manual_session`; `state` JSON default dict; `version` PositiveInteger default 0; `confirmed_at` nullable DateTime; `minutes` Text blank; timestamps.

ProgressUpdate additions: `occurred_on` nullable indexed date; `snapshot` JSON default dict blank; `applied_to_task` Boolean default True. Existing call sites unchanged.

State shape: `{title, meeting_date: YYYY-MM-DD, meeting_time: HH:MM or '', agenda, project_ids: [int], person_ids: [int], items: [...]}`.

Item shape: `{id: UUID string, kind: task|new_task|risk|decision|note, task_id: int|null, project_id: int|null, person_id: int|null, title, status, progress: int|'', due_date: YYYY-MM-DD|'', planned_for: YYYY-MM-DD|'', completed_work, next_step, content, recorded: bool, baseline: signed-string}`. Unrecorded task cards never apply. Text fields may be empty while drafting. One linked task per session. Task baseline is signed by server catalog and holds original id/updated_at/status/progress/project/assignee/title/dates. New tasks and risk/decision require project at confirm; generic notes do not. Existing-task person_id is reporting person, not reassignment.

Review refinement: task items also return read-only `baseline_values: {status,progress,title,project_name,assignee_name,due_date,planned_for}`, always derived from the signed baseline by server normalization/serialization. Client initially copies the selected catalog values; this cannot override server baseline validation. “No change” and original-state labels use this snapshot, never a newer catalog state. Editing actual task progress fields marks the card recorded automatically; merely choosing a task/reporter does not, and manual unchecking can exclude it.

Service interface (`core/services/manual_meetings.py`):
- `ManualMeetingError(message, conflict=False)` exposes `.conflict`.
- `task_option(task)` -> catalog dict `{id,title,project_id,project_name,assignee_id,assignee_name,status,status_label,progress,due_date,planned_for,current_note,baseline}`.
- `create_session(state)` -> MeetingSession; title/date required; defaults other fields.
- `save_session(pk, version, state)` -> MeetingSession; normalized full state, version+1.
- `preview_session(session)` -> `{items: [{id,kind,title,project_name,person_name,summary,historical}], errors: [str], historical: bool, counts: {tasks,new_tasks,risks,notes}, minutes: str}`. Validates without writes.
- `confirm_session(pk, version)` -> confirmed MeetingSession; validates preview, guards version and current task baseline, atomically applies once; repeat same session returns already confirmed without replay.
- `serialize_session(session)` -> `{id,note_id,version,state,confirmed,updated_at,minutes}`.

HTTP routes (root owns urls/views):
- `manual_meeting_create`: GET/POST `/meetings/manual/new/`, form title/date/time/agenda/projects/people, redirects workspace.
- `manual_meeting_workspace`: GET `/meetings/manual/<pk>/`, renders core/manual_meeting_workspace.html. Context `session_data` from serializer plus `urls:{save,preview,confirm,reference,list}`; `catalog:{projects:[{id,name}],people:[{id,name}],tasks:[task_option],statuses:[{value,label}]}`. Session pk, not note pk.
- `manual_meeting_save`: POST JSON `{version,state}`, response `{ok:true,...serialize_session}`; errors `{ok:false,error,conflict}` status400/409.
- `manual_meeting_preview`: GET same session preview renders core/manual_meeting_preview.html, context `meeting_session, preview, session_data`; confirm HTML POST field version redirects workspace. Error displays preview, no data loss.
- `manual_meeting_reference`: POST JSON `{kind:project|person,name}`, response `{ok:true,kind,item:{id,name}}`; safe validated inline create.
- workspace autosave must flush before preview/navigation, show failures, retain local edited state on 409. No saving confirmed workspace.

### Task 1: Durable domain and confirmation

Owner: backend implementer. Files: core/models.py, core/migrations/0005_manual_meetings.py (generated name may differ), core/services/manual_meetings.py; split cohesive validation/render helpers if module would grow beyond ~500 lines; tests/test_manual_meetings.py. No views/templates/reports edits.

- [ ] Write behavior tests for draft/resume without formal changes, stable IDs/duplicate linked task, incomplete draft vs confirm validation, version conflict, once-only confirm, unchanged unrecorded tasks, reporter not reassignment, risk/decision/task links by ID, historical-only task update, future date rejection, stale baseline rejection, transaction rollback, safe malformed inputs.
- [ ] Run RED with `UV_CACHE_DIR=/tmp/meeting-progress-uv-cache uv run pytest tests/test_manual_meetings.py -q`.
- [ ] Implement shared model/API contracts. Limit request draft to 300 items and reasonable text length. Signed baselines must match linked task. Use CAS write to session version to acquire SQLite write lock before reading/applying tasks; stale writes cannot overwrite. Missing/nonexistent references must produce readable errors.
- [ ] Task updates use current model validations. Done shortcut means 100%; business completion time for today's meeting uses meeting time, entry audit stays now. Historical snapshots use signed baseline and do not save existing Task at all.
- [ ] Store immutable final markdown minutes in session/note raw_text; normal notes retained there; structured records retain source_meeting. Snapshot includes title/project_name/person_name. Risks use source meeting for report date fallback.
- [ ] Run focused tests/migration check, report RED/GREEN evidence and files; do not commit.

### Task 2: Workspace frontend

Owner: frontend implementer. Files: templates/core/manual_meeting_form.html, templates/core/manual_meeting_workspace.html, templates/core/manual_meeting_preview.html, static/css/meeting-workspace.css, static/js/meeting-workspace.js, tests/js/meeting-workspace.test.cjs. Root handles base/nav and shared views. Follow shared contracts exactly.

- [ ] Write JS tests first for duplicate selection/stable IDs, selecting person preserving inputs, serial autosave, failed saves retaining dirty state, conflict blocking auto-overwrite, editing during save preserving new content, flush-before-preview and no navigation on failed save, preventing double confirm.
- [ ] Implement compact two-pane people/task workspace, loading/empty/error/readonly states. Add/update/new-task/risk/decision/note cards with explicit recorded flag; quick done/progress/blocked/no-change; next step, deadlines, planned_for. Catalog searches unfinished by person/project and optional completed task switch. Include incomplete/missing-reference affordances.
- [ ] Metadata editable in details, people selection state, per-person recorded counts, inline project/person creation without losing fields. Create form uses Django `form` fields (root provides title, meeting_date, meeting_time, agenda, projects, people).
- [ ] Debounced serial server save (~700ms), timeout through body consumption, retry, conflict notice, unload guard, explicit save/exit. Use returned state only when no newer edits; keep latest user values otherwise. Do not depend on app.js submit-once for JSON actions.
- [ ] Save before preview. Preview shows exact changed rows/errors/historical notice and POST confirmation version+csrf; confirmed workspace read-only markdown with copy button. Escape all user content; no innerHTML interpolation of user text.
- [ ] Add actual behavior tests, run node tests, report evidence; do not commit.

### Task 3: HTTP integration, history and entry points

Owner: controller. Files: core/manual_meeting_views.py, core/manual_meeting_forms.py, core/urls.py, core/views.py, core/services/reports.py, core/services/exports.py, core/services/dashboard.py if needed for pending count correctness, templates/base.html, templates/core/meeting_list.html, templates/core/meeting_form.html, tests/test_manual_meeting_views.py, tests/test_reports_and_exports.py, README.md.

- [ ] Write failing HTTP tests for authenticated create/resume, API errors/CSRF, pending list, redirects for manual notes, AI parse rejection for manual, confirm/readonly, export durability, business-date report fallback.
- [ ] Implement endpoints/metadata form and integration using Task 1 contracts; pending list includes manual drafts and distinguishes manual finalized from AI parsing. Retain existing meeting_create URL as paste input; primary new entry manual, secondary paste.
- [ ] Add exports for sessions and occurrence fields; no secrets. Report project/title snapshot and date fallback.
- [ ] Run focused and full Python/JS tests, migration dryrun/check, git diff --check.

### Task 4: Review, visual QA and local release

Owner: controller + independent reviewer. No new user features.

- [ ] Review backend task, frontend task and complete integration against approved design; fix material findings with regression tests.
- [ ] Isolated fixture browser flow desktop/mobile: create meeting, inline refs, add old/new task, note/risk, autosave/reload, person switching, network failure, preview/confirm once, read-only copy. Check screenshots/overflow/focus.
- [ ] Back up actual container SQLite before applying migration. Build/start app+worker with persisted volume, health/read-only route checks; do not add test records or call LLM in actual app.
- [ ] Inspect ignore boundaries/status/index, commit exact code/docs/tests, push main using previous explicit authorization; concise handoff with URL/checks/limitations.
