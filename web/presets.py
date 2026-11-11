'''
The amp's presets as the server knows them: one copy of each slot's stored preset
(Fender's preset JSON), for the knobs in the tone dialog. Filled from a backup of
maneline's mmp_backup.py, and kept current by the amp bridge: when the amp's name for
a slot differs from the copy's, the page asks the bridge to read the slot again.

  python -m web.presets import BACKUP_DIR      fill the copy from a backup
  python -m web.presets levels FILE            import a level survey (maneline's mmp_level.py
                                               survey); mmp/match-preset-levels.sh sends it
                                               through the amp bridge's link instead
'''

import json
import os
import sys

SCHEMA = """
CREATE TABLE IF NOT EXISTS amp_presets (
    slot    int not null primary key,
    name    text,
    json    text not null,
    fetched text);
"""


def cached(conn, slot):
    '''(name, preset JSON) of a slot as last read, or None.'''
    row = conn.execute("SELECT name, json FROM amp_presets WHERE slot = ?", (slot,)).fetchone()
    return (row[0], json.loads(row[1])) if row else None


def store(conn, slot, name, doc):
    conn.execute("INSERT OR REPLACE INTO amp_presets (slot, name, json, fetched) VALUES (?, ?, ?, datetime('now'))",
                 (slot, name, json.dumps(doc)))
    conn.commit()


def import_backup(conn, backup):
    '''All slots of a backup (manifest.json + one file per slot); returns their number.
    The name is the preset's own (an audition can leave another name in the manifest).'''
    conn.executescript(SCHEMA)
    manifest = json.load(open(os.path.join(backup, 'manifest.json'), encoding='utf-8'))
    for entry in manifest['presets']:
        doc = json.load(open(os.path.join(backup, entry['file']), encoding='utf-8'))
        store(conn, entry['slot'], doc.get('info', {}).get('displayName') or entry['name'], doc)
    return len(manifest['presets'])


def main(argv=None):
    import ChordsPlay.db
    from web import app
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2 or argv[0] not in ('import', 'levels'):
        print('\n'.join(line.strip() for line in __doc__.strip().splitlines()[-4:]))
        return 2
    db = ChordsPlay.db.Db(app.DB_PATH, app.DEFAULT_USER)
    try:
        if argv[0] == 'levels':
            with open(argv[1], encoding='utf-8') as f:
                print('levels imported:', db.importPresetLevels(json.load(f)))
        else:
            print('presets imported:', import_backup(db.conn, argv[1]))
    finally:
        db.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
