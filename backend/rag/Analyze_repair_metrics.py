"""
analyze_repair_metrics.py
==========================
Lit un fichier de logs serveur (stdout redirigé, journalctl, etc.),
extrait les lignes `[repair_metric] {...}` émises par
stream_handler.py._repair_metric(), et affiche les stats utiles pour
décider où investir (JSON schema, alias supplémentaires, nouvelle
representation dédiée, etc.) plutôt que deviner.

Usage:
    python analyze_repair_metrics.py server.log
    # ou en direct depuis un service qui tourne:
    journalctl -u ton-service --since today | python analyze_repair_metrics.py -
"""
import sys
import json
import re
from collections import Counter, defaultdict

_TAG = "[repair_metric] "


def parse_events(lines):
    events = []
    for line in lines:
        idx = line.find(_TAG)
        if idx == -1:
            continue
        raw = line[idx + len(_TAG):].strip()
        try:
            events.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return events


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)

    src = sys.argv[1]
    lines = sys.stdin if src == "-" else open(src, encoding="utf-8", errors="replace")
    events = parse_events(lines)

    if not events:
        print("Aucune ligne [repair_metric] trouvée. "
              "As-tu bien déployé stream_handler.py v3.4 et laissé tourner un peu ?")
        return

    total_turns = sum(1 for e in events if e["event"] in ("ok_no_repair", "invalid_first_attempt"))
    ok_no_repair = sum(1 for e in events if e["event"] == "ok_no_repair")
    triggered    = sum(1 for e in events if e["event"] == "invalid_first_attempt")
    repaired_ok  = sum(1 for e in events if e["event"] == "repair_ok")
    exhausted    = sum(1 for e in events if e["event"] == "repair_exhausted")

    print("=" * 60)
    print("  REPAIR METRICS")
    print("=" * 60)
    print(f"  Tours analysés              : {total_turns}")
    if total_turns:
        print(f"  Widget correct du 1er coup  : {ok_no_repair}  "
              f"({ok_no_repair / total_turns:.1%})")
        print(f"  Repair déclenché            : {triggered}  "
              f"({triggered / total_turns:.1%})")
    if triggered:
        print(f"    → réparé avec succès      : {repaired_ok}  "
              f"({repaired_ok / triggered:.1%} des cas déclenchés)")
        print(f"    → épuisé (widget vide)    : {exhausted}  "
              f"({exhausted / triggered:.1%} des cas déclenchés)")

    # Quelle representation échoue le plus au 1er essai ?
    fail_by_rep = Counter(
        e.get("attempted_representation") or "(inconnue — JSON/clé invalide)"
        for e in events if e["event"] == "invalid_first_attempt"
    )
    if fail_by_rep:
        print("\n  Échecs au 1er essai par representation :")
        for rep, n in fail_by_rep.most_common():
            print(f"    {rep:<25} {n}")

    # Quelles erreurs reviennent le plus souvent (texte brut) ?
    err_counter = Counter(
        re.sub(r"\d+", "N", e.get("error", ""))  # normalise les nombres pour regrouper
        for e in events if e["event"] == "invalid_first_attempt" and e.get("error")
    )
    if err_counter:
        print("\n  Messages d'erreur les plus fréquents (1er essai) :")
        for err, n in err_counter.most_common(10):
            print(f"    [{n:>3}]  {err[:100]}")

    # Quelles lesson_number génèrent le plus d'exhausted (widget vide livré) ?
    exhausted_by_lesson = Counter(
        e.get("lesson_number") for e in events if e["event"] == "repair_exhausted"
    )
    if exhausted_by_lesson:
        print("\n  Widgets vides livrés, par leçon (priorise ici) :")
        for lesson, n in exhausted_by_lesson.most_common():
            print(f"    leçon {lesson:<10} {n}")

    print("=" * 60)


if __name__ == "__main__":
    main()