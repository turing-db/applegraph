# Apple Graph

Your Mac quietly keeps a detailed record of your life: who you message and call, which apps you use and when, what you browse, what you listen to, who appears in your photos and where. Apple Graph reads those on-device stores and loads them into [TuringDB](https://docs.turingdb.ai) as one connected graph that you can query and explore visually.

It reads these sources:

| Source | What it adds |
|---|---|
| Apple's knowledge graph (`~/Library/IntelligencePlatform`) | People, apps, calendar events, places, organisations |
| knowledgeC.db | App usage, web usage, notifications, Bluetooth, screen time, Siri intents (about the last four weeks) |
| Biome streams | Messages and calls per person, music and media played, app use on your iPhone and iPad |
| Safari history | Domains visited per day |
| Contacts | Names, phones, emails, birthdays, organisations |
| Mail | Who emails whom, how often (no subjects or bodies) |
| Photos | Face clusters, who appears with whom, places and dates |

Everything stays on your Mac. The importer never contacts a network service, and the graph lives in a `.apple-graph/` folder inside the project.

## Set up on your Mac

### 1. Check what you need

- **macOS 15 Sequoia or later.** Apple's knowledge graph folder, which the importer requires, does not exist on older versions. Check with `sw_vers -productVersion`.
- **About 1.5 GB of free disk** for Python, TuringDB and the graph. A large photo library or message history needs more.
- **Admin rights** to grant Full Disk Access in step 3.

The other sources are optional. If you don't use Mail or Photos on this Mac, those parts are skipped.

### 2. Install uv

[uv](https://docs.astral.sh/uv/) manages Python and the dependencies. It downloads Python 3.14 for you, so you don't need to install Python yourself.

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh     # or: brew install uv
```

Open a new terminal window afterwards so `uv` is on your PATH. Then copy or clone this project folder and install it:

```sh
git clone git@github.com:turing-db/appleintelligence.git
cd appleintelligence
uv sync --locked
```

This installs the published `turingdb` package from PyPI, which includes both the database server and the visualizer.

### 3. Give your terminal Full Disk Access

macOS protects these databases, so the terminal app you run the importer from needs permission to read them.

1. Open **System Settings > Privacy & Security > Full Disk Access**.
2. Click **+** and add your terminal app, such as Terminal, iTerm, Ghostty or the VS Code app.
3. Turn its switch on, then **quit and reopen the terminal**. The permission only applies to newly started processes.

Check that it worked:

```sh
ls ~/Library/IntelligencePlatform ~/Library/Biome/streams/restricted | head
```

If you see `Operation not permitted`, the permission has not taken effect yet. Quit the terminal fully with Cmd+Q and try again.

### 4. Import your data

```sh
uv run apple-graph import --entities-only
```

This takes a minute or two. It snapshots each source read-only, builds the graph and writes it to `.apple-graph/` as a graph named `apple_life`. It prints node and edge counts when it is done. The `--entities-only` flag leaves out a raw archive of every SQLite row, which is useful for forensics but clutters the visualizer.

To leave a source out, add `--skip`, for example `--skip mail --skip photos`. To point at a source in a non-standard place, see Troubleshooting below.

### 5. Start the server and open the visualizer

```sh
uv run turingdb -turing-dir "$PWD/.apple-graph" -load apple_life -p 16666 -i 127.0.0.1 -ui -ui-port 18080 -demon
```

Run it from the project folder. The server keeps running in the background. Then open http://127.0.0.1:18080 and select **apple_life** in the graph picker. The server also loads an empty graph called `default`, and queries against that one return nothing.

A good first query to paste into the visualizer:

```cypher
MATCH p = (me:Me)-[r:MESSAGES_WITH|CALLS_WITH|APPEARS_WITH]-(x:Person) WHERE r.messages >= 100 OR r.calls >= 10 OR r.photos >= 20 RETURN p LIMIT 150
```

It draws you in the middle with a spoke to everyone you talk to or are photographed with. Lower the thresholds if it comes back empty.

To stop the server later:

```sh
kill $(lsof -tnP -iTCP:16666 -sTCP:LISTEN)
```

### 6. Explore

The project ships 61 ready-made queries. List them and print any one as a single line to paste into the visualizer:

```sh
uv run apple-graph queries
uv run apple-graph queries --category visual
uv run apple-graph queries trip --param place=Paris
```

Or query from Python. Start a shell with `uv run python` and run:

```python
from apple_graph.queries import connect, run, show
db = connect()                    # the server from step 5, graph apple_life
show()                            # every query with a description
run(db, 'inner_circle')           # returns a pandas DataFrame
db.query("MATCH (p:Person) RETURN p.name, p.photos_faces ORDER BY p.photos_faces DESC LIMIT 10")
```

From the shell, `uv run apple-graph --port 16666 query '<cypher>'` runs a single query against the server.

### 7. Name the people Photos doesn't know

Most face clusters have no name, so they appear as `Face cluster 12` or `person 4051…`. To see who one is, find its ID and reveal its photos in the Photos app:

```sh
uv run apple-graph queries unnamed_faces           # paste into the visualizer or run() it for IDs
uv run apple-graph faces <apple_id or photos_person_uuid> --reveal 3
```

The first time, macOS asks whether your terminal may control Photos. Then either name the person in Photos' People album, or add them to `.apple-graph/names.json`:

```json
{"<photos_person_uuid or apple_id>": "Alex", "com.toyopagroup.picaboo": "Snapchat"}
```

Names apply on the next import, so refresh as described below.

### 8. Refresh with newer data

TuringDB 3.1 cannot delete or overwrite a graph, so either import under a new name:

```sh
uv run apple-graph --graph apple_life_2 import --entities-only
```

and select `apple_life_2` in the visualizer, or stop the server, delete the old graph and import again:

```sh
kill $(lsof -tnP -iTCP:16666 -sTCP:LISTEN)
rm -rf .apple-graph/graphs/apple_life .apple-graph/data/apple_life.*
uv run apple-graph import --entities-only
```

Then start the server again as in step 5.

To remove everything, stop the server and delete the `.apple-graph/` folder.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `No graph.db and ontology.db in ...` or `Operation not permitted` | Full Disk Access is missing or the terminal wasn't restarted. See step 3. On macOS 14 or earlier the knowledge graph doesn't exist. |
| `Graph already exists` | Use a new `--graph` name or delete the old one. See step 8. |
| The visualizer shows nothing | Select **apple_life** in the graph picker. The server starts on `default`. |
| `Address already in use` when starting the server | A server is already running. Stop it with the `kill` command from step 5, or keep using it. |
| A query errors with "chunk ended before its columns did" | Avoid `labels(n)` in results. The native client can't decode it for nodes with several labels. Use `n.entity_type`. |
| Mail is missing | Older macOS versions keep Mail under `V9` or `V8`. Find yours with `ls ~/Library/Mail` and pass `--mail ~/Library/Mail/V9/MailData/Envelope\ Index`. |
| Photos is missing | Pass the library's database: `--photos "/path/to/Library.photoslibrary/database/Photos.sqlite"`. |
| Few people have real names | That's how Apple stores it. Name them as in step 7. |
| Message counts look low | Biome only keeps recent history, and only for devices signed into the same Apple Account with iCloud sync on. |

All source locations can be overridden on `import` with `--knowledge`, `--safari`, `--contacts`, `--mail`, `--photos`, `--biome` and `--source`. Run `uv run apple-graph import --help` for the full list.

## Privacy

The importer only reads local files and never sends anything over the network. The `.apple-graph/` folder holds a copy of your personal data, so don't commit it, share it or sync it to cloud storage. The project's `.gitignore` already excludes it. If you query the graph through an AI assistant, whatever results the assistant reads are sent to its provider like any other conversation content.

## Reference

The rest of this document describes the graph model in detail.

Use `--data-dir PATH` before the subcommand to choose a different TuringDB directory. `import --export-only` writes the JSONL without loading it into TuringDB. Without `--entities-only`, the import adds Apple's ontology and a lossless archive of every SQLite row from the Apple graph and knowledgeC, which is what the `apple_full` example graph contains. While the server is running, pass `--port 16666` to `verify` and `query` instead of opening a second embedded engine over the same directory.

The default sources are read under `~/Library` and `~/Pictures`. SQLite sources are snapshotted read-only with the backup API, which includes committed WAL contents. Each database is snapshotted separately, so they are not a single atomic cross-database snapshot. Biome stream files are read directly.

### Query catalogue

`apple_graph.queries` holds 61 ready-made queries in three categories:

- **insight**: tables about your habits, such as who you talk to most, busiest days and app hours per device.
- **inference**: what Apple can infer by joining sources, such as sleep times, lonely days, drifting friendships, home from photos and your Siri action ledger.
- **visual**: queries that return whole paths, sized for the visualizer, such as your social star, group chats as hubs and trips.

From the shell, list them or print one as a single line, with `$parameters` filled in, ready to paste into the visualizer:

```sh
uv run apple-graph queries                                  # every query
uv run apple-graph queries --category visual
uv run apple-graph queries trip --param place=Paris     # one pasteable line
```

From Python, against the running server:

```python
from apple_graph.queries import connect, run, show, one_line
db = connect()                       # 127.0.0.1:16666, graph apple_life
show('inference')
run(db, 'sleep_schedule')
run(db, 'day_reconstruction', date='2025-12-24')
print(one_line('person', person='Face cluster 12'))
```

Parameters used across the catalogue are `app`, `apple_id`, `date`, `domain`, `month`, `person` and `place`. `tests/test_queries.py` loads a synthetic graph into a throwaway embedded TuringDB and runs every query both with parameters and as a pasted line. TuringDB 3.1 has no `shortestPath`, so `shortest_social_path` sorts variable-length paths by length instead. Timestamps such as `first_start` are UTC ISO strings, so the sleep times are in UTC.

### Mapping

Apple stores its graph as subject/predicate/object rows in `graph.db`, with multirow "relationships" sharing a `relationshipId`. The importer turns those rows into an ordinary property graph:

- **Entities** are `AppleEntity` nodes with an extra label from their `is a` class: `Person`, `Software`, `CalendarEvent`, `Organization`, `Place`, `GroupOfHumans`. `apple_id` keeps Apple's subject ID as a string; `entity_type` keeps the decoded class text.
- **Literal facts become properties**, keyed by the predicate label in snake case (`bundle_id`, `first_name`, `virtual_conference_url`). Repeated values are joined with ` | `. Apple's JSON calendar-date objects are rendered as `2026-09-18 12:45 Europe/London`. `name` is the display name: the `name` fact, else first/family name, bundle id, application identifier, alias, address, city, URL, or description, else `<class> <id>`.
- **References become edges** typed by the predicate label in upper snake case, for example `(:Software)-[:PUBLISHED_BY]->(:Organization)`. Apple writes references as `md:<subject id>` or `<identifier type>:<identifier id>`; the latter are resolved through the identifier groups that entities declare. Every fact edge carries Apple's provenance: `table_name`, `active`, `predicate`, `raw_object`, `confidence`, `sources`, `source_duplicates`, and `timestamp_utc` (Apple seconds since 2001, as UTC).
- **Relationship groups that point at another entity become one edge per reference**, with the group's attribute facts as edge properties. There are no intermediate relationship nodes: `(:CalendarEvent)-[:HAS_ATTENDEE {participant_obligation, via: 'attendee identifier', relationship_id, relationship_type, confidence, timestamp_utc}]->(:Person)`. A trailing "relationship" is dropped from the type, so `has location relationship` becomes `HAS_LOCATION`.
- **Attribute-only relationship groups are flattened** onto the entity: aliases, identifiers, contact details, visual identifiers, extraction tags, and dates. Pairing inside a group (which contact label belongs to which phone number) is not preserved there; it remains in the raw-row archive.
- `same as` values almost always name the entity's own source record (`cnContact:`, `phPerson:`, `lsBundleRecord:`), so self-references stay as the `same_as` property and only genuine cross-entity matches become `SAME_AS` edges. `ekEvent:`/`sgEvent:` values are hashes of the entity's own identifier and are reported as unresolved in the manifest.
- Properties and labels come from the current `stable_graph` and `event_graph` tables, with stable values winning. Expired tables contribute only edges and relationship nodes marked `active = false`; their literal values remain in the archive.
- Ontology classes and predicates are `AppleClass`/`ApplePredicate` nodes with `DOMAIN`, `RANGE`, and `SUBCLASS_OF` edges (full graph only).

#### Timeline from knowledgeC.db

`knowledgeC.db` is the CoreDuet knowledge store: the event log behind Siri Suggestions, Screen Time, and Apple Intelligence. Each `ZOBJECT` row is a timed event in a named stream, with optional structured metadata and a source app/device. The importer aggregates those events per local calendar day onto `Day` nodes (`date`, `weekday`, `screen_on_seconds`), splitting intervals at local midnight and counting overlapping intervals once:

- `/app/usage` becomes `(:Software)-[:USED_ON {seconds, minutes, sessions, share_of_day, first_start, last_end}]->(:Day)`; `share_of_day` is the fraction of that day's total app time, handy as an edge weight. Apps are matched to existing `Software` entities by bundle id; unknown bundle ids get a new `Software` node with `source = knowledgeC`. Totals land on the app as `usage_seconds` and `usage_sessions`.
- `/app/webUsage` becomes `WebDomain` nodes with the same per-day `BROWSED_ON` edges, plus one `BROWSED {seconds, minutes, visits, days, first_seen, last_seen}` edge from the browser app.
- `/app/mediaUsage` becomes `PLAYED_MEDIA_ON`, `/notification/usage` becomes `NOTIFIED_ON {notifications}`, `/bluetooth/isConnected` becomes `BluetoothDevice` nodes with `CONNECTED_ON`.
- `/app/intents` (calls, messages, media, Maps lookups donated from your devices) become edges typed by the intent verb, such as `(:Software)-[:START_CALL {direction, local_time, timestamp_utc, device, intent_class, donated_by_siri, uuid}]->(:Day)`. Apple strips contact handles from the synced interactions, so they cannot point at `Person` nodes.
- `/display/isBacklit` feeds `screen_on_seconds` on each day. Sync bookkeeping and discoverability streams are archive-only.
- Calendar events from the Apple graph get `SCHEDULED_ON {start_time, end_time, all_day, occurrence, occurrences}` edges to the same days, so meetings, app usage, browsing, and calls share one timeline.
- Every `Day` belongs to a `Month` through `IN_MONTH`.

#### People across sources

Phone numbers and email addresses are normalised into one handle index, so a contact reached by WhatsApp, a call, an email and the Apple graph lands on the same `Person` node. The address book's owner record, or the Apple graph's current-user flag, marks the node labeled `Me`; messaging and call summaries hang off it. People only known from a messaging app get the display name the app recorded; mail senders not matched to a person become `Correspondent` nodes.

#### Naming people yourself

Photos usually names only the people you have named yourself, so people who only appear in photos show up as `person <id>` or `Face cluster <n>`. Three ways to fix that, all picked up on the next import:

- Find out who a cluster is with `uv run apple-graph faces <apple_id> --reveal 3`, which lists the cluster's photos (date, face size, whether the original is on disk) and selects the first three in the Photos app so you can see the face and name it on the spot. `--open N` opens locally stored originals instead; libraries on optimised iCloud storage usually have none.
- Name the person in the Photos app (People & Pets album). Names sync through iCloud Photos and land in `ZFULLNAME`, which the importer prefers over placeholders.
- Put a JSON file at `.apple-graph/names.json` (or pass `import --names FILE`) mapping an `apple_id`, Photos person UUID (`photos_person_uuid`), phone or email handle, contact id or app bundle id to the name you want, for example `{"405112233445566778": "Alex", "com.toyopagroup.picaboo": "Snapchat"}`. A real name that gets replaced is kept in `other_names`.

#### AddressBook

Contacts matched through their `cnContact` identifier enrich the existing person with `first_name`, `family_name`, `nickname`, `organization`, `job_title`, `department`, `birthday`, labelled `phones`, `emails` and `postal_addresses`; unmatched contacts become new `Person` nodes with `source = AddressBook`.

#### Safari history

`(:WebDomain)-[:VISITED_ON {visits, pages, first_visit, last_visit}]->(:Day)` per domain and day over the whole retained history (a year here), with `safari_visits`, `safari_pages`, `safari_first_visit` and `safari_last_visit` on the domain. Domains visited only once are skipped. Page titles and URLs are not stored.

#### Mail envelope index

`(:sender)-[:EMAILED {messages, to, cc, bcc, conversations, first, last}]->(:recipient)` aggregated per pair, and `(:sender)-[:EMAILED_ON {messages, recipients}]->(:Day)`. Spam, bin, junk and draft mailboxes are skipped. Subjects and bodies are not stored.

#### Photos library

Face clusters that match the Apple graph's `phPerson` identifiers, carry a name, or have at least five faces become or enrich `Person` nodes with `photos_faces`, `photos_person_uuid` and `photos_verified`. Edges: `(:Person)-[:PHOTOGRAPHED_ON {photos}]->(:Day)`, `(:Person)-[:APPEARS_WITH {photos, first, last}]->(:Person)` for pairs sharing at least two photos, and `(:Day)-[:PHOTOGRAPHED_AT {photos}]->(:Place)` from moment titles, with approximate coordinates on the place. Days carry `photos_taken` and `videos_taken`. Clusters you never named in Photos stay anonymous until you name them.

#### Biome streams

Biome stream files are SEGB v2 containers of protobuf records (`apple_graph/segb.py`). Apple publishes no schema, so field numbers were mapped empirically and are documented in `apple_graph/biome.py`; records synced from other devices are attributed to them.

- **Siri.Remembers.MessageHistory**: one record per message with app, time, direction and participants. `(:Person|GroupChat)-[:MESSAGED_ON {messages, sent, received, app}]->(:Day)` and `(:Me)-[:MESSAGES_WITH {messages, sent, received, first, last, apps}]->(:Person|GroupChat)`; group members get `MEMBER_OF_CHAT`. Message text is not present in the stream.
- **Siri.Remembers.CallHistory**: `CALLED_ON {calls, incoming, outgoing, talk_seconds, app}` and `CALLS_WITH`. Direction follows the convention observed for messages and may be inverted for some apps.
- **Media.NowPlaying**: `MediaItem {title, artist, album, duration_seconds}` nodes with `PLAYED_ON {plays, app, device}` per day and `PLAYED {plays}` from the player app. A play is a distinct five-minute bucket.
- **App.InFocus** (synced devices only): focus start/stop pairs become `USED_ON` edges with `device` set to the phone or tablet model, alongside the Mac's knowledgeC usage.

Every table in all top-level IntelligencePlatform `*.db` databases and in `knowledgeC.db` is also archived in the full graph (the other sources are aggregated only, not archived): each table is an `AppleTable` linked to its `AppleDatabase`, and each original row is an `AppleSQLiteRow` with lossless `row_json` (integers as decimal strings, blobs as base64, floats as hexadecimal strings, explicit nulls). Non-database files in `Artifacts/` and lock files are outside this SQLite import.

Node and edge counts are checked after import. `verify` compares counts, every archived SQLite row's full contents, and node display names against the JSONL export. Each export also writes a manifest with per-table counts, label counts, edge type counts, and unresolved reference counts.

```sh
uv run python -m unittest discover -s tests -v
```

`tests/test_import.py` covers the Apple graph and knowledgeC mapping; `tests/test_sources.py` builds synthetic Safari, AddressBook, Mail, Photos and SEGB fixtures for the other importers.

TuringDB JSONL format: https://docs.turingdb.ai/import_data/jsonl
