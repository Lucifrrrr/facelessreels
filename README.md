# Faceless Reels – Mars Colony

Vollautomatische Instagram-Serie: Eine persistente 3D-Marskolonie (Blender) wächst mit
jedem Follower. Die Community stimmt per Kommentar (A/B) über das nächste Gebäude ab.

## Ablauf (täglich, `python -m mars.pipeline --mode publish`)

| # | Schritt | Modul |
|---|---------|-------|
| 1 | Followerzahl abrufen | `mars/instagram.py` |
| 2–5 | Einwohner berechnen, Abstimmung auswerten, Meilensteine + Gebäude dauerhaft hinzufügen | `mars/world.py`, `mars/votes.py` |
| 6 | Drehbuch (Vorlagen, deutsch) | `mars/story.py` |
| 7 | Deutscher Narrator (Piper „thorsten“, offline, kostenlos) | `mars/audio.py` |
| 8 | 3D-Rendering (Blender EEVEE, 1080×1920) + 1 Higgsfield-Clip (5 s) | `blender/colony_scene.py`, `mars/higgs.py` |
| 9 | Schnitt: Musik + Effekte (selbst erzeugt, rechtefrei), Untertitel, Zähler, Abstimmungskarte | `mars/compose.py` |
| 10 | Technische + inhaltliche Prüfung | `compose.quality_check` |
| 11 | Beschreibung + Hashtags | `mars/story.py` |
| 12–13 | Reel hochladen (Resumable Upload) + Status prüfen | `mars/instagram.py` |
| 14–15 | Neue Abstimmung + Weltzustand speichern | `mars/pipeline.py` |

## Zustand & Sicherheit

- `state/mars/world.json` – dauerhafte Welt (Gebäude werden nie automatisch entfernt)
- `state/mars/runs/` – Checkpoints je Lauf (Wiederaufnahme nach Abbruch)
- `state/budget_ledger.json` – zentrale Kostenerfassung (Limit 150 €/Monat gesamt, 45 € Higgsfield)
- `state/mars/activation.json` – öffentliche Veröffentlichung erst nach Freigabe
- `state/mars/alerts.json`, `failures.json`, `publish_log.json` – Protokolle
- Zugangsdaten nur als Umgebungsvariablen/Secrets: `HF_KEY`, `IG_ACCESS_TOKEN`, `IG_USER_ID`, `GH_SECRETS_TOKEN`

## Abstimmungsregeln

Gültig: Kommentar beginnt mit A oder B, innerhalb des Zeitraums (Veröffentlichung bis +23,5 h),
eine Stimme pro Account (erste zählt). Gleichstand: Die Option, deren erste Stimme früher kam,
gewinnt; ohne Stimmen gewinnt A.

## Lokal testen

```bash
python -m mars.pipeline --mode test --followers 137 --no-higgsfield --fresh
python tests/test_logic.py
```
