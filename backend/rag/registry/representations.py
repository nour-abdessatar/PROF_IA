"""
registry/representations.py
============================
Le catalogue fixe (donné au LLM comme menu de choix) + une fonction pure
par représentation ("layout_xxx"). Chaque fonction prend des PARAMÈTRES
SÉMANTIQUES (nombres, labels, positions logiques) — jamais de pixels
inventés par le LLM — et retourne une liste de steps au format déjà
consommé par le moteur SVG animé existant (prompt_builder.CANVAS_ENGINE_
SHELL / render_canvas_widget()) :

    [{"label": str, "ops": [ops...]}]

ops = {"type": "line"|"arc"|"dot"|"text", ...}  — même format qu'avant.

RÈGLE DE CE FICHIER : aucune fonction ici ne doit jamais halluciner une
coordonnée. Toute position doit être soit une constante documentée, soit
calculée par une formule/boucle/algorithme déterministe à partir des
paramètres reçus. C'est ce qui élimine la classe d'erreur "chevauchement/
débordement" qui existait dans l'ancien système à coordonnées libres —
voir tests/test_registry.py pour la couverture de non-régression.

Note sur "geometry_generic" / "freeform_svg": le catalogue ne contient
qu'un seul échappatoire ("freeform_svg") pour les cas non couverts par une
représentation dédiée — il n'y a pas de fonction layout_geometry_generic()
séparée, cette distinction n'apportait rien (les deux finissaient par le
même chemin "pas de fonction dédiée"). stream_handler.py route
"freeform_svg" AVANT d'atteindre ce fichier (validation locale légère,
voir ce fichier-là), donc aucune fonction ici ne le gère.

Note sur "none" (v27.2): troisième clé spéciale, également routée dans
stream_handler.py AVANT d'atteindre ce fichier — pas de layout_none().
Contrairement à "freeform_svg" (dessin libre pour un cas non couvert),
"none" signifie "pas de widget du tout, l'explication textuelle suffit".
Ajoutée parce que le seul échappatoire disponible avant elle était
"freeform_svg", ce qui poussait structurellement le modèle à forcer un
dessin même sur un point purement conceptuel où aucun visuel n'apporte
rien — "none" retire cette pression sans reproduire le problème qu'on
cherchait à éliminer (aucune coordonnée à générer, donc rien à halluciner
ni à valider). Exclue explicitement du tracking used_representations
(voir stream_handler.extract_used_representations()) — sinon choisir
"none" correctement sur un tour pousserait le tour suivant à l'éviter,
ce qui est l'inverse de l'effet recherché.
"""
from __future__ import annotations

import math
from collections import Counter
from typing import Optional

# ─────────────────────── CONSTANTES PARTAGÉES ───────────────────────

W, H = 480, 300  # doit matcher le viewBox de CANVAS_ENGINE_SHELL (prompt_builder.py)

_SUPERSCRIPT_DIGITS = {"0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴",
                        "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹"}


def _superscript(n: int) -> str:
    return "".join(_SUPERSCRIPT_DIGITS[d] for d in str(n))


def _check_bounds(x: float, y: float, margin: int = 15, context: str = "") -> None:
    """
    Garde-fou générique: si un layout calcule une position hors canvas, on
    préfère lever une erreur explicite (→ InvalidParams côté dispatch.py,
    → repair retry côté stream_handler.py) plutôt que de silencieusement
    produire un dessin coupé/invisible.
    """
    if not (margin <= x <= W - margin and margin <= y <= H - margin):
        raise ValueError(f"position ({x:.0f},{y:.0f}) hors canvas {context}")


# ═══════════════════════ CATALOGUE (menu LLM) ═══════════════════════

# ─────────────────── INTENTS (v32) ───────────────────
# A second, orthogonal tag axis alongside "subjects". "subjects" answers
# "which school subject" (math/geometry) — "intents" answers "what KIND
# of teaching moment is this", independent of subject, independent of
# lesson. This is the fix for a repeating bug class: every time a lesson
# point needed representation-choice reasoning that wasn't already
# spelled out verbatim in prompt_builder.py's geo_note_ar, the model
# either picked the nearest-sounding option (wrong) or fell to "none"/
# freeform_svg (safe but low-value) — and the fix each time was one more
# hand-written 🔴 clause. That doesn't scale to "any lesson, any
# subject". Tagging each representation ONCE with the kind(s) of
# teaching moment it serves, and surfacing that tag directly in the menu
# (see build_menu below), gives the model a small, fixed, reusable
# vocabulary to classify content against instead of memorized
# per-lesson prose. Keep this vocabulary small and generic — do not add
# an intent for a single lesson's sake; if nothing here fits, that's a
# signal a representation (or a new variant of an existing one) is
# genuinely missing, same as the circle_line_position/demo_point_label
# gaps found in practice.
#
#   definition  — introducing/naming a concept for the first time
#   construction — step-by-step "how do you build/draw this"
#   property    — demonstrating a rule/invariant that holds generally
#   relation    — how two+ things relate to each other (position, type)
#   comparison  — comparing/positioning quantities or sets directly
#   operation   — a worked arithmetic/algebraic procedure
#
# A representation may (and often should) carry more than one intent —
# e.g. compass_construction covers BOTH "construction" (plain build
# steps) AND "property" (via demo_point_label) for the same variant.
# Any representation with 2+ intents is, by definition, legitimately
# reusable across a lesson for genuinely different teaching moments —
# see get_variant_carrying_representations() below, which derives that
# set from this tag instead of a manually maintained list.
INTENT_LABELS_AR = {
    "definition": "تعريف/تسمية مفهوم",
    "construction": "خطوات إنشاء/بناء",
    "property": "خاصية/قاعدة عامة",
    "relation": "علاقة بين عنصرين أو أكثر",
    "comparison": "مقارنة/تموضع كميات",
    "operation": "عملية حسابية موضّحة خطوة بخطوة",
}
INTENT_LABELS_FR = {
    "definition": "définition/nommer un concept",
    "construction": "étapes de construction",
    "property": "propriété/règle générale",
    "relation": "relation entre deux éléments ou plus",
    "comparison": "comparaison/positionnement de quantités",
    "operation": "procédure arithmétique détaillée",
}


def get_variant_carrying_representations() -> set[str]:
    """Représentations légitimement réutilisables dans une même leçon —
    dérivé de "intents" (2+ intents = sert plusieurs moments pédagogiques
    distincts) plutôt qu'une liste maintenue à la main. Remplace l'ancien
    _VARIANT_CARRYING_REPRESENTATIONS dupliqué séparément dans
    prompt_builder.py ET stream_handler.py (risque de désync — l'un des
    deux avait déjà pris du retard sur l'autre en pratique, voir
    compass_construction avant ce fix). Single source of truth ici;
    ajouter une representation à ce set ne nécessite plus AUCUN
    changement de code ailleurs, seulement lui donner un second tag
    "intents" ci-dessous.
    """
    return {k for k, v in REPRESENTATIONS.items() if len(v.get("intents", [])) > 1}


REPRESENTATIONS = {
    "number_line": {
        "when_ar": "كمية بسيطة أو نقاط على خط مرقم (أعداد، مقارنة، تموضع)",
        "when_fr": "une quantité simple ou des points sur une droite numérique",
        "intents": ["comparison", "definition"],
        "params_schema": {
            "points": "list[{value: number, label: str, color?: str}]",
            "min_value": "int?", "max_value": "int?",
        },
    },
    "dot_grouping": {
        "when_ar": "تجميع محسوس وقابل للعدّ — قسمة كمشاركة، توضيح باقٍ بشكل ملموس",
        "when_fr": "regroupement concret dénombrable — division comme partage, reste illustré",
        "subjects": ["math"],
        "intents": ["operation"],
        "params_schema": {
            "total": "int", "group_size": "int",
            "dividend_label": "str", "divisor_label": "str",
            "quotient_label": "str", "remainder_label": "str",
        },
    },
    "symbolic_formula": {
        "when_ar": "قاعدة/صيغة رمزية عامة صالحة لأي عدد أو متغيّر (خصائص، متطابقات، توزيعية) — "
                   "🔴 كل معادلة كاملة (رمز + دليل + = + الطرف الآخر) هي قائمة واحدة "
                   "(formula واحدة)، وليست عدة formulas متتالية — كل عنصر في formulas "
                   "الخارجية يُرسم كسطر منفصل تحت سابقه، فتقسيم معادلة واحدة على عنصرين "
                   "يُنتج رمزين متراكبين عمودياً بدل معادلة أفقية واحدة. 🔴 subscript=true "
                   "يُوضع فقط على رمز الدليل نفسه (مثال: الدليل n في Dₙ)، وليس على الرمز "
                   "الأساسي قبله (D) ولا على علامة \"=\" أو ما بعدها — غالباً معادلة مثل "
                   "\"Dₙ = {...}\" تُكتب كثلاث/أربع عناصر: {text:'D'} (بدون subscript)، "
                   "{text:'n', subscript:true}، ثم {text:'= {...}'} (بدون subscript)",
        "when_fr": "règle/formule symbolique générale valable pour tout nombre ou variable — "
                   "🔴 une équation complète (symbole + indice + = + membre de droite) est "
                   "UNE seule formule (une seule liste interne), jamais plusieurs formulas "
                   "successives — chaque élément de la liste formulas externe est dessiné "
                   "sur sa propre ligne sous la précédente, donc scinder une équation en "
                   "deux entrées produit deux symboles empilés verticalement au lieu d'une "
                   "équation horizontale. 🔴 subscript=true ne s'applique qu'au symbole de "
                   "l'indice lui-même (ex. le n de Dₙ), jamais au symbole de base qui le "
                   "précède (D) ni au signe \"=\" ou à ce qui suit — une équation comme "
                   "\"Dₙ = {...}\" s'écrit typiquement en 3-4 termes : {text:'D'} (sans "
                   "subscript), {text:'n', subscript:true}, puis {text:'= {...}'} (sans "
                   "subscript)",
        "intents": ["property", "definition"],
        "params_schema": {
            "formulas": "list[list[{text: str, color?: str, exponent?: bool, subscript?: bool}]]  "
                        "— 1 formule = 1 ligne/équation complète ; subscript seulement sur "
                        "le terme-indice, pas sur le symbole de base ni sur '='",
        },
    },
    "venn_diagram": {
        "when_ar": "المجموعات، التقاطع، الاتحاد، الاحتواء — 🔴 إذا كان للمجموعة "
                   "عناصر ملموسة في المثال (أرقام أو أسماء)، مررها في elements "
                   "لتُرسم داخل الدائرة نفسها، مثل [\"1\", \"3\", \"5\"] — لا تترك "
                   "الدائرة فارغة بينما النص يذكر عناصر محددة. استعمل "
                   "notation_lines فقط لسطر رمزي إضافي أسفل الرسم (كتابة "
                   "المجموعة بصيغة A = {...} أو علاقة كالتقاطع)، وليس كبديل "
                   "لعرض العناصر داخل الدائرة",
        "when_fr": "ensembles, intersection, union, inclusion — 🔴 si l'ensemble "
                   "a des éléments concrets dans l'exemple (nombres ou noms), "
                   "passe-les dans elements pour qu'ils soient dessinés à "
                   "l'intérieur du cercle, ex. [\"1\", \"3\", \"5\"] — ne laisse "
                   "pas le cercle vide alors que le texte cite des éléments "
                   "précis. notation_lines sert uniquement à une ligne "
                   "symbolique supplémentaire sous le dessin (écriture "
                   "A = {...} ou une relation comme l'intersection), pas de "
                   "substitut pour afficher les éléments dans le cercle",
        "subjects": ["math"],
        "intents": ["relation", "comparison"],
        "params_schema": {
            "sets": "list[{name: str, color?: str, elements?: list[str|int]}]  (1 ou 2 éléments)",
            "notation_lines": "list[str]?",
            "band_index": "int?  (0, 1, 2… pour empiler plusieurs diagrammes indépendants)",
        },
    },
    "euclidean_division": {
        "when_ar": "القسمة الإقليدية بعناصرها الأربعة (المقسوم، القاسم، خارج القسمة، الباقي)",
        "when_fr": "division euclidienne avec ses 4 éléments (dividende, diviseur, quotient, reste)",
        "subjects": ["math"],
        "intents": ["operation"],
        "params_schema": {
            "dividend": "int", "divisor": "int", "quotient": "int", "remainder": "int",
            "dividend_label": "str", "divisor_label": "str",
            "quotient_label": "str", "remainder_label": "str",
            "subtractions": "list[{subtract: int, remainder_after: int}]?",
        },
    },
    "factor_tree": {
        "when_ar": "تفكيك عدد إلى جداء عوامل أولية (شجرة التفكيك)",
        "when_fr": "décomposition en produit de facteurs premiers (arbre)",
        "subjects": ["math"],
        "intents": ["operation", "construction"],
        "params_schema": {
            "tree": "{value: int, prime: bool, left?: tree, right?: tree}",
        },
    },
    "mirror_symmetry": {
        "when_ar": "التناظر المحوري (نقطة، مستقيم، زاوية، دائرة)",
        "when_fr": "symétrie axiale (point, droite, angle, cercle)",
        "subjects": ["geometry"],
        "intents": ["construction", "property"],
        "params_schema": {
            "variant": "'point'|'circle'|'line_special_cases'|'angle'",
            "axis_x": "int? (défaut 240)",
        },
    },
    "compass_construction": {
        "when_ar": "إنشاء بالبركار (المنصف العمودي، عمودي بواسطة نقطة، منصف الزاوية)",
        "when_fr": "construction au compas (médiatrice, perpendiculaire par un point, bissectrice d'un angle)",
        "subjects": ["geometry"],
        "intents": ["construction", "property"],
        # BUGFIX: this used to document "a": "[x, y]", "b": "[x, y]" as
        # params the LLM should supply — i.e. it asked the model to invent
        # raw canvas pixel coordinates itself, which is exactly the bug
        # class this whole registry system was built to eliminate (see
        # dispatch.py/representations.py module docstrings). The function
        # still accepts optional a/b internally for flexibility (now
        # bounds-checked — see layout_compass_construction), but the menu
        # shown to the LLM no longer advertises them: only semantic labels
        # are requested, and the layout always places the segment itself.
        #
        # v28: added "angle_bisector" variant. Every arc/point it draws
        # (D, E, F below) is computed from the actual ray angles via real
        # trig — never hallucinated pixels — which is what fixes the
        # "arc doesn't match any ray from vertex" class of error that
        # freeform_svg used to produce for this exact construction.
        #
        # v29 BUGFIX: "when_ar"/"when_fr" above have advertised "عمودي
        # بواسطة نقطة" / "perpendiculaire par un point" since before v28,
        # but no variant ever implemented it — every call for this case
        # had no valid menu option and fell through to freeform_svg
        # (confirmed via a live trace: the model hand-computed arc
        # intersections itself for the "point on line" case, correctly
        # this time, but with no structural guarantee of that on the next
        # turn — exactly the error class the registry exists to remove).
        # Split the vague single claim into the two real constructions a
        # 7ème lesson actually distinguishes and added both below, same
        # real-trig/no-hallucinated-pixel discipline as angle_bisector.
        "params_schema": {
            "variant": "'perpendicular_bisector'|'angle_bisector'|'perpendicular_on_line'|'perpendicular_external_point'",
            "a_label": "str", "b_label": "str",
            "demo_point_label": "str? (perpendicular_bisector uniquement, ex. 'P' — si la leçon porte sur la PROPRIÉTÉ (tout point du médiateur est équidistant de A et B), fournis cette lettre pour faire apparaître un point de démonstration avec les segments PA/PB et 'PA=PB'; omets ce champ pour la simple construction sans propriété)",
            "vertex_label": "str? (angle_bisector uniquement, défaut 'O')",
            "angle_degrees": "number? (angle_bisector uniquement, défaut 60 — ouverture de l'angle à bissecter)",
            "d_label": "str? (angle_bisector uniquement, défaut 'D', point sur le rayon OA)",
            "e_label": "str? (angle_bisector uniquement, défaut 'E', point sur le rayon OB)",
            "f_label": "str? (angle_bisector uniquement, défaut 'F', point sur la bissectrice)",
            "line_label": "str? (perpendicular_on_line/perpendicular_external_point uniquement, défaut '(d)')",
            "point_label": "str? (perpendicular_on_line/perpendicular_external_point uniquement, défaut 'A' — le point par lequel passe la perpendiculaire)",
            "point1_label": "str? (perpendicular_on_line/perpendicular_external_point uniquement, défaut 'P1' — premier point où l'arc coupe (d); si le [TEACHER] nomme ce point (ex. 'M'), fournis ici la MÊME lettre)",
            "point2_label": "str? (perpendicular_on_line/perpendicular_external_point uniquement, défaut 'P2' — second point où l'arc coupe (d); doit matcher le [TEACHER] de la même façon, ex. 'N')",
            "foot_label": "str? (perpendicular_external_point uniquement, défaut 'H' — pied de la perpendiculaire sur la droite)",
            "method": "'compass'|'direct'? (perpendicular_on_line/perpendicular_external_point uniquement, défaut 'compass' — 'direct' dessine directement la perpendiculaire avec l'angle droit marqué, SANS les arcs au compas)",
            "m_label": "str? (défaut 'M' — ATTENTION: pour perpendicular_on_line/perpendicular_external_point ce label désigne le point d'INTERSECTION DES ARCS (l'apex), pas les points sur (d) — si le [TEACHER] appelle CE point par une autre lettre (ex. 'P'), fournis-la ici, distincte de point1_label/point2_label)",
        },
    },
    "angle_pair": {
        "when_ar": (
            "زاوية واحدة أو أنواع الزوايا (حادة، قائمة، منفرجة، متتامتان، "
            "متكاملتان، متقابلتان بالرأس). استعمل \"generic\" لتعريف/تسمية "
            "زاوية بسيطة (رأس + ضلعان) بدون قياس بالدرجات — مثلاً درس تعريف "
            "الزاوية نفسه، قبل الحديث عن أنواعها. استعمل \"adjacent\" لعرض "
            "ثلاثة أشعة من نفس الرأس (زاويتان متجاورتان تشتركان في ضلع) — "
            "كل زاوية تُسمّى تلقائياً بترميز widehat (مثل AOB) مرسوم فوق "
            "الحروف، وليس رمز ∠ العادي."
        ),
        "when_fr": (
            "un angle simple ou types d'angles (aigu, droit, obtus, "
            "complémentaires, supplémentaires, opposés). Utilise \"generic\" "
            "pour définir/nommer un angle simple (sommet + 2 côtés) sans "
            "mesure en degrés — ex. la leçon de définition de l'angle "
            "elle-même, avant d'aborder ses types. Utilise \"adjacent\" pour "
            "trois rayons partageant le même sommet (deux angles adjacents "
            "partageant un côté) — chaque angle est nommé automatiquement "
            "avec la notation widehat (ex. AOB) dessinée au-dessus des "
            "lettres, pas le symbole ∠ brut."
        ),
        "subjects": ["geometry"],
        "intents": ["definition", "relation"],
        "params_schema": {
            "kind": "'generic'|'acute'|'right'|'obtuse'|'complementary'|'supplementary'|'vertical'|'adjacent'",
            "measure_degrees": "number? (ignoré pour 'generic'/'adjacent'; sinon ou measure_a_degrees/measure_b_degrees pour les paires)",
            "vertex_label": "str? (défaut 'O')",
            "ray_a_label": "str? (défaut 'A')",
            "ray_b_label": "str? (défaut 'B')",
            "measure_degrees": "number? (ignoré pour 'generic'/'adjacent'; sinon ou measure_a_degrees/measure_b_degrees pour les paires)",
            "ray_c_label": "str? ('vertical': nom du rayon opposé à A, si pas de notation A'/B' — défaut ray_a_prime_label ou 'A''; 'adjacent': défaut 'C')",
            "ray_d_label": "str? ('vertical' uniquement: nom du rayon opposé à B, si pas de notation A'/B' — défaut ray_b_prime_label ou 'B'')",
            "angle_name": "str? (uniquement pour 'generic'; défaut ray_a_label+vertex_label+ray_b_label, ex. 'AOB')",
            "angle_aob_degrees": "number? (uniquement pour 'adjacent', défaut 35)",
            "angle_boc_degrees": "number? (uniquement pour 'adjacent', défaut 45)",
        },
    },
    "triangle_angle_sum": {
        "when_ar": "مجموع زوايا المثلث (180°) — رسم مثلث مسمى برؤوسه وزواياه الثلاث",
        "when_fr": "somme des angles d'un triangle (180°) — triangle nommé avec ses 3 sommets/angles",
        "subjects": ["geometry"],
        "intents": ["property"],
        "params_schema": {
            "variant": "'scalene'|'right'|'isosceles' (défaut 'scalene')",
            "a_label": "str? (défaut 'A')", "b_label": "str? (défaut 'B')", "c_label": "str? (défaut 'C')",
            "show_sum_formula": "bool? (défaut true)",
        },
    },
    "quadrilateral_angle_sum": {
        "when_ar": "مجموع زوايا الرباعي (360°) — رسم رباعي مسمى برؤوسه وزواياه الأربع",
        "when_fr": "somme des angles d'un quadrilatère (360°) — quadrilatère nommé avec ses 4 sommets/angles",
        "subjects": ["geometry"],
        "intents": ["property"],
        "params_schema": {
            "variant": "'generic'|'rectangle'|'parallelogram' (défaut 'generic')",
            "a_label": "str? (défaut 'A')", "b_label": "str? (défaut 'B')",
            "c_label": "str? (défaut 'C')", "d_label": "str? (défaut 'D')",
            "show_sum_formula": "bool? (défaut true)",
        },
    },
    "circle_line_position": {
        "when_ar": (
            "الوضعيات النسبية لدائرة ومستقيم — قاطع (يقطعها في نقطتين)، "
            "مماس (يمسها في نقطة واحدة)، أو خارجي عنها (لا يقطعها ولا "
            "يمسها) — بناءً على مقارنة البعد من مركز الدائرة إلى "
            "المستقيم مع نصف القطر"
        ),
        "when_fr": (
            "positions relatives d'un cercle et d'une droite — sécante "
            "(coupe en 2 points), tangente (touche en 1 point), ou "
            "extérieure (ne coupe ni ne touche) — selon la comparaison "
            "entre la distance du centre à la droite et le rayon"
        ),
        "subjects": ["geometry"],
        "intents": ["relation", "definition"],
        "params_schema": {
            "variant": "'secant'|'tangent'|'exterior'",
            "center_label": "str? (défaut 'O')",
            "radius_label": "str? (défaut 'r')",
            "line_label": "str? (défaut '(d)')",
            "foot_label": (
                "str? (pied de la perpendiculaire du centre à la droite; "
                "pour 'tangent' c'est aussi le point de tangence, sauf si "
                "point_label est fourni; défaut 'H')"
            ),
            "point_label": "str? ('tangent' uniquement: nom du point de tangence, si différent de foot_label)",
            "point_a_label": "str? ('secant' uniquement: premier point d'intersection, défaut 'E')",
            "point_b_label": "str? ('secant' uniquement: second point d'intersection, défaut 'F')",
        },
    },
    "none": {
        "when_ar": "النقطة مفاهيمية/تعريفية بحتة ولا يضيف أي رسم قيمة حقيقية — الشرح النصي وحده كافٍ",
        "when_fr": "point purement conceptuel/définitionnel où aucun dessin n'apporte de valeur réelle — l'explication textuelle seule suffit",
        "params_schema": None,  # aucun widget produit — voir stream_handler._parse_and_build_widget
    },
    "freeform_svg": {
        "when_ar": "أي حالة نادرة جداً لا يغطيها أي مما سبق (شكل هندسي عام غير معتاد مثلاً)",
        "when_fr": "cas très rare non couvert par ce qui précède (ex: figure géométrique inhabituelle)",
        "params_schema": None,  # échappatoire — voir stream_handler._parse_and_build_widget
    },
}


def build_menu(lang: str, subject: str = "") -> str:
    """Le bloc de prompt donné au LLM — remplace tous les hints prose
    dédiés de l'ancien prompt_builder.py (pré-v25).

    v25.2: inclut désormais params_schema pour chaque representation, pas
    seulement la description when_ar/when_fr. Sans ça, le LLM n'avait
    aucune idée des noms de clés attendus dans "params" et devait les
    deviner à chaque appel (groupSize vs group_size, labels imbriqués vs
    labels à plat, etc.) — voir le changelog / l'incident dot_grouping
    qui a motivé ce fix. build_menu() est la SEULE source de vérité que
    le modèle voit pour "params"; si params_schema n'y figure pas, il
    n'existe nulle part dans le prompt.

    v27.0: filtrage optionnel par `subject` ("math" | "geometry" | "").
    Chaque entrée de REPRESENTATIONS peut porter une clé "subjects"
    (liste, ex. ["geometry"]) — si présente, l'entrée n'apparaît dans le
    menu que si `subject` y figure. Les entrées SANS clé "subjects"
    (actuellement: symbolic_formula, number_line, freeform_svg) restent
    toujours visibles quel que soit le sujet: une formule ou une droite
    numérique peuvent légitimement servir en géométrie comme en
    arithmétique, et freeform_svg est l'échappatoire universelle — les
    contraindre par sujet aurait retiré une option valide sans gagner de
    précision. Filtrage fail-open: si `subject` est vide/inconnu (valeur
    absente du lesson dict, sujet futur non encore taggé), AUCUNE entrée
    n'est filtrée — le comportement est identique à avant ce changement.
    Objectif: réduire le nombre d'options non pertinentes montrées au
    modèle (ex. compass_construction pour une leçon d'arithmétique) sans
    jamais pouvoir masquer une representation valide par erreur.
    v32: chaque entrée peut désormais porter "intents" (voir bloc de
    constantes juste au-dessus de REPRESENTATIONS) — affiché en tête de
    chaque ligne du menu comme [construction/property/...]. Objectif:
    remplacer les correctifs ad hoc empilés dans geo_note_ar (un 🔴 par
    cas rencontré) par un vocabulaire fixe et réutilisable que le modèle
    applique lui-même à N'IMPORTE QUEL contenu, leçon ou matière future
    — pas seulement les cas déjà vus en prod. Une representation sans
    "intents" (ex. freeform_svg) n'affiche aucun tag.
    """
    import json
    key = "when_ar" if lang == "ar" else "when_fr"
    intent_key = INTENT_LABELS_AR if lang == "ar" else INTENT_LABELS_FR
    subject = (subject or "").strip().lower()
    legend = (
        "أنواع اللحظات التعليمية (اختر بناءً على طبيعة المحتوى، ليس فقط اسمه): "
        + "، ".join(f"{k}={v}" for k, v in INTENT_LABELS_AR.items())
        if lang == "ar" else
        "types de moments pédagogiques (choisis selon la NATURE du contenu, pas juste son nom): "
        + ", ".join(f"{k}={v}" for k, v in INTENT_LABELS_FR.items())
    )
    lines = [legend, ""]
    for k, v in REPRESENTATIONS.items():
        allowed_subjects = v.get("subjects")
        if allowed_subjects and subject and subject not in allowed_subjects:
            continue
        intents = v.get("intents")
        tag = f"[{'/'.join(intents)}] " if intents else ""
        schema = v.get("params_schema")
        if schema is not None:
            schema_str = json.dumps(schema, ensure_ascii=False)
            lines.append(f"- {tag}{k}: {v[key]}\n  params: {schema_str}")
        else:
            # freeform_svg — pas de schema fixe, géré séparément par
            # CANVAS_STEPS_JSON_EXAMPLE_FREEFORM dans prompt_builder.py
            lines.append(f"- {tag}{k}: {v[key]}")
    return "\n".join(lines)

# ═══════════════════════ LAYOUT: number_line ═══════════════════════

def layout_number_line(points: list[dict],
                        min_value: Optional[float] = None,
                        max_value: Optional[float] = None) -> list[dict]:
    if not points:
        raise ValueError("number_line requires at least one point")

    values = [p["value"] for p in points]
    lo = min_value if min_value is not None else min(0, min(values))
    hi = max_value if max_value is not None else max(values) + 1
    span = max(hi - lo, 1)

    x0, x1, y = 50, 430, 150

    def sx(v):
        return x0 + (v - lo) / span * (x1 - x0)

    axis_ops = [{"type": "line", "from": [x0, y], "to": [x1, y],
                 "color": "primary", "lineWidth": 2.5}]
    v = math.ceil(lo)
    while v <= math.floor(hi):
        tx = sx(v)
        axis_ops.append({"type": "dot", "at": [tx, y], "color": "text", "radius": 2})
        axis_ops.append({"type": "text", "text": str(v), "at": [tx - 5, y + 20], "color": "text"})
        v += 1
    steps = [{"label": "", "ops": axis_ops}]

    for p in points:
        px = sx(p["value"])
        _check_bounds(px, y, context="number_line point")
        color = p.get("color", "hi")
        label = str(p.get("label", p["value"]))
        steps.append({"label": label, "ops": [
            {"type": "dot", "at": [px, y], "color": color, "radius": 6},
            {"type": "text", "text": label, "at": [px - 8, y - 15], "color": color},
        ]})
    return steps


# ═══════════════════════ LAYOUT: dot_grouping ═══════════════════════

def layout_dot_grouping(total: int, group_size: int,
                         dividend_label: str, divisor_label: str,
                         quotient_label: str, remainder_label: str) -> list[dict]:
    if group_size <= 0:
        raise ValueError("group_size must be > 0")
    if total <= 0:
        raise ValueError("total must be > 0")

    DOT_R, COL_GAP, ROW_GAP, COLS = 6, 32, 32, 4

    left_x0, left_y0 = 55, 55

    # BUGFIX: the old guard was `if total > 60: raise ValueError(...)`, but
    # that number didn't match this layout's actual geometry — with
    # COLS=4/ROW_GAP=32, the dividend column's dot grid + its label already
    # run off the bottom of the canvas (H=300) well before total reaches
    # 60 (around total=25, depending on exact margin), and nothing here
    # ever checked that. The dots/label were silently placed off-canvas
    # for any total roughly in the 25-60 range — the exact class of bug
    # this whole registry system exists to eliminate, just hiding behind
    # a plausible-looking but wrong constant. Fixed by computing the real
    # bottom-most y used by the left column and checking it against the
    # canvas height directly, so this stays correct even if COLS/ROW_GAP
    # ever change, instead of relying on a hand-picked number again.
    rows_used = (total - 1) // COLS + 1
    label_y = left_y0 + rows_used * ROW_GAP + 15
    if label_y > H - 15:
        raise ValueError(
            f"total={total} too large for dot_grouping's canvas height "
            "(dividend column would overflow) — use euclidean_division instead"
        )

    dividend_ops = [
        {"type": "dot", "at": [left_x0 + (i % COLS) * COL_GAP,
                                 left_y0 + (i // COLS) * ROW_GAP],
         "color": "primary", "radius": DOT_R}
        for i in range(total)
    ]
    dividend_ops.append({"type": "text", "text": f"{total} ({dividend_label})",
                          "at": [left_x0 - 15, label_y], "color": "text"})
    steps = [{"label": str(total), "ops": dividend_ops}]

    n_groups = total // group_size
    remainder = total % group_size
    right_x0, right_y0, ROW_GAP_GROUP = 280, 55, 26

    if right_x0 + group_size * COL_GAP > W - 15:
        raise ValueError("group_size too large — groups would overflow canvas width")

    group_ops = []
    for g in range(n_groups):
        gy = right_y0 + g * ROW_GAP_GROUP
        for j in range(group_size):
            group_ops.append({"type": "dot", "at": [right_x0 + j * COL_GAP, gy],
                               "color": "secondary", "radius": DOT_R})
    steps.append({"label": f"{divisor_label} = {group_size}", "ops": group_ops})

    q_y = right_y0 + n_groups * ROW_GAP_GROUP + 20
    if q_y > H - 15:
        raise ValueError("too many groups for canvas height — use euclidean_division instead")
    steps.append({"label": f"{quotient_label} = {n_groups}", "ops": [
        {"type": "text", "text": f"{n_groups} ({quotient_label})", "at": [right_x0, q_y], "color": "accent"}
    ]})

    if remainder:
        rem_y = q_y + 35
        if rem_y > H - 15:
            raise ValueError("remainder row overflows canvas height")
        rem_ops = [
            {"type": "dot", "at": [right_x0 + k * COL_GAP, rem_y], "color": "hi", "radius": DOT_R}
            for k in range(remainder)
        ]
        rem_ops.append({"type": "text", "text": f"{remainder} ({remainder_label})",
                         "at": [right_x0, rem_y + 22], "color": "hi"})
        steps.append({"label": f"{remainder_label} = {remainder}", "ops": rem_ops})

    return steps


# ═══════════════════════ LAYOUT: symbolic_formula ═══════════════════════

def layout_symbolic_formula(formulas: list[list[dict]]) -> list[dict]:
    """
    formulas: liste de formules; chaque formule est une liste de termes
      {"text": str, "color"?: str="text", "exponent"?: bool=False}
    Chaque formule = un step, sur sa propre ligne de base. Les formules
    successives sont empilées verticalement avec un écart fixe (jamais de
    collision inter-step, contrairement à l'ancien système où le LLM
    devait calculer cet espacement lui-même).
    """
    if not formulas:
        raise ValueError("symbolic_formula requires at least one formula")

    _KNOWN_TERM_KEYS = {"text", "color", "exponent", "subscript"}

    steps = []
    y, Y_GAP = 90, 70
    for formula in formulas:
        if not formula:
            continue
        # BUGFIX: this used to check `y > H - 20` AFTER already advancing y
        # past the row just appended — i.e. it validated the position of a
        # hypothetical NEXT formula that might never exist, not the row it
        # had just actually placed. That rejected perfectly valid stacks
        # (e.g. exactly 3 formulas fit at y=90/160/230, well within
        # H-20=280, but the loop still raised on the 3rd iteration because
        # it checked the *next* y=300 before returning). Checking the
        # about-to-be-used position up front instead means: only ever
        # reject a formula whose OWN row would actually be off-canvas,
        # never one whose row is fine but happens to be the last.
        if y > H - 20:
            raise ValueError("too many stacked formulas for canvas height")
        x = 90
        ops = []
        for term in formula:
            # BUGFIX: this used to only ever check term.get("exponent"),
            # silently ignoring any other key (e.g. "subscript" — needed
            # for things like D_n) and drawing that term as plain
            # normal-baseline text instead. Fail loudly on unrecognized
            # keys instead, same discipline as _check_bounds elsewhere in
            # this file: a silently-dropped flag produces a wrong-looking
            # but "successful" render, which is worse than a caught error.
            unknown = set(term) - _KNOWN_TERM_KEYS
            if unknown:
                raise ValueError(
                    f"symbolic_formula: unknown term key(s) {unknown} in "
                    f"{term!r} — add support instead of letting them be ignored"
                )
            if term.get("exponent") and term.get("subscript"):
                raise ValueError(
                    f"symbolic_formula: term {term['text']!r} can't be both "
                    f"exponent and subscript"
                )

            text = term["text"]
            if term.get("exponent"):
                ty = y - 15
            elif term.get("subscript"):
                ty = y + 10
            else:
                ty = y
            op = {"type": "text", "text": text, "at": [x, ty],
                  "color": term.get("color", "text")}
            if term.get("exponent") or term.get("subscript"):
                op["font"] = "11px sans-serif"
            ops.append(op)
            x += 25 + len(text) * 7
        if x > W - 20:
            raise ValueError(f"formula too wide for canvas: {''.join(t['text'] for t in formula)!r}")
        label = "".join(t["text"] for t in formula)
        steps.append({"label": label, "ops": ops})
        y += Y_GAP

    return steps


# ═══════════════════════ LAYOUT: venn_diagram ═══════════════════════

def _place_set_elements(cx: float, cy: float, r: float, elements: list,
                         color: str) -> list[dict]:
    """Place a set's elements as small text labels arranged in a compact
    grid centered inside its circle. Raises ValueError if they don't fit
    the given radius — same guard style as dot_grouping's overflow check,
    so a bad call fails loudly instead of drawing off/outside the arc.
    Used only for the single-set case now — see _place_elements_at() for
    the two-set case, which needs sub-regions smaller than a full circle.
    """
    if not elements:
        return []
    COLS = 1 if len(elements) <= 3 else 2
    ROW_GAP, COL_GAP, PAD = 16, 30, 14
    rows = (len(elements) - 1) // COLS + 1
    total_h = (rows - 1) * ROW_GAP
    total_w = (COLS - 1) * COL_GAP
    if total_h / 2 + PAD > r or total_w / 2 + PAD > r:
        raise ValueError(
            f"{len(elements)} elements don't fit inside a venn_diagram circle "
            f"of radius {r} — pass fewer elements or use notation_lines instead"
        )
    start_x = cx - total_w / 2
    start_y = cy - total_h / 2
    ops = []
    for i, el in enumerate(elements):
        col, row = i % COLS, i // COLS
        ops.append({"type": "text", "text": str(el),
                    "at": [start_x + col * COL_GAP, start_y + row * ROW_GAP],
                    "color": color})
    return ops


def _place_elements_at(cx: float, cy: float, elements: list, color: str,
                        max_w: float, max_h: float, zone: str,
                        force_single_col: bool = False,
                        row_gap: float = 16) -> list[dict]:
    """Same compact-grid placement as _place_set_elements(), but scoped to
    an explicit (cx, cy, max_w, max_h) box instead of "centered inside a
    full circle of radius r" — needed for the two-set venn case, where
    only-in-A / only-in-B / common elements each live in a sub-region
    (roughly a third of the diagram's width) rather than the whole circle.
    `zone` is just for the error message, so a fit failure says which
    region overflowed (e.g. "common (overlap)" vs "قواسم 12 (left-only)").

    `force_single_col`: the overlap lens between two circles is narrow —
    a 2-column split there visually straddles the circle boundary line.
    Pass True to always stack vertically instead, regardless of count.

    `row_gap`: the two-circle overlap region (a "lens"/vesica shape) is
    narrowest away from its vertical center and widest at the middle —
    a tall single column can put its outer rows in a part of the lens
    that's barely wider than a single glyph, visually touching the arc.
    Smaller row_gap keeps a short column closer to the lens's widest
    point. See layout_venn_diagram's common-zone call for the actual
    geometry check that motivated this.
    """
    if not elements:
        return []
    COLS = 1 if (force_single_col or len(elements) <= 3) else 2
    ROW_GAP, COL_GAP = row_gap, 26
    rows = (len(elements) - 1) // COLS + 1
    total_h = (rows - 1) * ROW_GAP
    total_w = (COLS - 1) * COL_GAP
    if total_h > max_h or total_w > max_w:
        raise ValueError(
            f"{len(elements)} elements don't fit in the '{zone}' zone of the "
            f"venn_diagram — pass fewer elements or use notation_lines instead"
        )
    start_x = cx - total_w / 2
    start_y = cy - total_h / 2
    ops = []
    for i, el in enumerate(elements):
        col, row = i % COLS, i // COLS
        ops.append({"type": "text", "text": str(el),
                    "at": [start_x + col * COL_GAP, start_y + row * ROW_GAP],
                    "color": color})
    return ops


def layout_venn_diagram(sets: list[dict],
                         notation_lines: Optional[list[str]] = None,
                         band_index: int = 0) -> list[dict]:
    if not (1 <= len(sets) <= 2):
        raise ValueError("venn_diagram supports 1 or 2 sets only")

    # BUGFIX (v2): r=58/cx-spacing=90 made the overlap lens too narrow at
    # its top/bottom taper (a vesica/almond shape — widest at its
    # vertical center, pinching to a point where the two arcs cross).
    # Measured: at the extreme rows of a 4-element common-zone stack, the
    # lens was only ~15.6px wide — barely a digit's width, crowding the
    # circle boundary lines. Bumped radius up and pulled the circles
    # closer together (more overlap) so the lens stays wide enough
    # (~35px+) across the vertical range a small common-elements stack
    # actually spans. only1/only2 offsets below were adjusted to match.
    r = 62 if band_index == 0 else 56
    y = 80 + band_index * 100
    if y + r > H - 10:
        raise ValueError("band_index too high — diagram would overflow canvas height")

    steps = []
    if len(sets) == 1:
        s = sets[0]
        cx = 190
        color = s.get("color", "primary")
        ops = [
            {"type": "arc", "cx": cx, "cy": y, "r": r, "startAngle": 0, "endAngle": 6.283,
             "color": color},
            {"type": "text", "text": s["name"], "at": [cx - 40, y - r - 10],
             "color": color},
        ]
        ops.extend(_place_set_elements(cx, y, r, s.get("elements"), color))
        steps.append({"label": s["name"], "ops": ops})
    else:
        # BUGFIX: this branch used to call _place_set_elements() with each
        # set's FULL element list, centered independently inside that
        # set's own circle — with zero awareness of the other set. Any
        # element shared between both sets (e.g. 1, 2, 3, 6 for the
        # common divisors of 12 and 18) got drawn TWICE, once inside each
        # circle's own column layout, landing near — but not inside — the
        # visual overlap lens, and with no connection to the other
        # occurrence. It looked like "1,3,6 / 2,4,12" on one side and
        # "1,3,9 / 2,6,18" on the other: an artifact of column-splitting
        # each set's list by position, not a computed intersection.
        #
        # Fix: actually compute common = set1 ∩ set2, only1 = set1 - set2,
        # only2 = set2 - set1, and place each in its own geometric zone —
        # common elements ONCE, in the overlap region between the two
        # circle centers; only1/only2 elements shifted toward the outer
        # (non-overlapping) side of their own circle. Comparison is done
        # on str(el) so mixed int/str element lists from the LLM still
        # dedupe correctly; display uses each element's original form
        # (whichever set's list is scanned first for a given value).
        s1, s2 = sets
        elements1 = s1.get("elements") or []
        elements2 = s2.get("elements") or []
        keys1 = [str(e) for e in elements1]
        keys2 = [str(e) for e in elements2]
        common_keys = [k for k in keys1 if k in keys2]
        only1 = [e for e, k in zip(elements1, keys1) if k not in common_keys]
        only2 = [e for e, k in zip(elements2, keys2) if k not in common_keys]
        # de-dupe common (in case a set's own list had a repeat) while
        # keeping first-seen order, then pull display values from set 1
        seen = set()
        common = []
        for e, k in zip(elements1, keys1):
            if k in common_keys and k not in seen:
                seen.add(k)
                common.append(e)

        # BUGFIX: at the previous cx1/cx2 spacing (90px apart, r=58), the
        # two-circle overlap lens was only 26px wide at its widest point
        # and narrowed to ~15.6px at the row where the outermost common
        # elements sit — barely wider than a single digit glyph, so items
        # like the top/bottom of a 4-element common column visually
        # touched the circle arcs instead of sitting clearly inside the
        # lens. Bringing the circles 20px closer together widens the lens
        # to ~39-46px across the same rows — comfortable clearance — while
        # still leaving enough room outside the overlap for the only1/
        # only2 zones (checked below).
        cx1, cx2 = 185, 255
        color1 = s1.get("color", "primary")
        color2 = s2.get("color", "secondary")

        ops1 = [
            {"type": "arc", "cx": cx1, "cy": y, "r": r, "startAngle": 0, "endAngle": 6.283,
             "color": color1},
            {"type": "text", "text": s1["name"], "at": [cx1 - 60, y - r],
             "color": color1},
        ]
        # only1 elements shifted left, away from the overlap lens, so they
        # don't collide with the common-elements text placed at the
        # midpoint below
        ops1.extend(_place_elements_at(cx1 - 30, y, only1, color1,
                                        max_w=50, max_h=2 * r - 30,
                                        zone=f"{s1['name']} (only)"))
        steps.append({"label": s1["name"], "ops": ops1})

        ops2 = [
            {"type": "arc", "cx": cx2, "cy": y, "r": r, "startAngle": 0, "endAngle": 6.283,
             "color": color2},
            {"type": "text", "text": s2["name"], "at": [cx2 + 35, y - r],
             "color": color2},
        ]
        # only2 elements shifted right, same reasoning as only1 above
        ops2.extend(_place_elements_at(cx2 + 35, y, only2, color2,
                                        max_w=50, max_h=2 * r - 30,
                                        zone=f"{s2['name']} (only)"))
        steps.append({"label": s2["name"], "ops": ops2})

        if common:
            # force_single_col: keeps the whole common set visually
            # merged inside the lens instead of splitting across the
            # circle-boundary seam. row_gap=13 (tighter than the default
            # 16): keeps a 4-element column's outer rows closer to the
            # lens's vertical center, where it's widest — see the cx1/cx2
            # note above for the actual geometry this is compensating for.
            common_ops = _place_elements_at(
                (cx1 + cx2) / 2, y, common, "text",
                max_w=30, max_h=2 * r - 20,
                zone="common (overlap)", force_single_col=True, row_gap=13,
            )
            steps.append({"label": "∩", "ops": common_ops})

    if notation_lines:
        # BUGFIX (v3): v2 anchored every notation_line at the RIGHT edge
        # (x = W - margin) on the assumption that prompt_builder.py's
        # _bidiAttrsFor() always resolves direction:"rtl" here, so
        # text-anchor:"start" would grow the text LEFTWARD from that
        # point and stay in bounds — true for a line that actually
        # contains Arabic (e.g. "القواسم المشتركة لـ 12 و 18…"), false
        # for a line that's pure Latin/symbols (e.g.
        # "D₁₂ ∩ D₁₈ = {1, 2, 3, 6}"): _bidiAttrsFor() only flips to rtl
        # when Arabic script is actually present, so a symbolic-only line
        # resolves ltr and grows RIGHTWARD from that same right-edge
        # anchor — straight off the canvas, leaving only its first couple
        # of characters visible (exactly the "D₁₂" fragment reported).
        # v2's fix was correct for Arabic and silently wrong for
        # French/symbolic notation lines, so direction is an unsafe axis
        # to anchor on regardless of which way you pick.
        #
        # Fix: stop guessing a direction-dependent start edge entirely —
        # anchor="middle" (already used for label centering elsewhere in
        # this file, e.g. angle-name caps above) centers the text on a
        # single x regardless of script/direction, so both Arabic and
        # Latin/symbolic lines land centered on the canvas with no
        # direction-detection needed.
        _NOTATION_MARGIN = 20
        _NOTATION_X = W / 2
        _CHAR_W_ESTIMATE = 9  # px/char at 14px font — conservative, covers Arabic+Latin
        max_chars = (W - 2 * _NOTATION_MARGIN) // _CHAR_W_ESTIMATE
        notation_y = y + r + 30
        for i, line in enumerate(notation_lines):
            if len(line) > max_chars:
                raise ValueError(
                    f"notation_line {i} is {len(line)} chars — too long to fit the "
                    f"canvas width (~{max_chars} chars max at this font size); "
                    f"shorten it or split it across two notation_lines entries"
                )
            ny = notation_y + i * 25
            if ny > H - 10:
                raise ValueError("too many notation_lines for canvas height")
            steps.append({"label": line, "ops": [
                {"type": "text", "text": line, "at": [_NOTATION_X, ny], "color": "text",
                 "anchor": "middle"}
            ]})
    return steps



# ═══════════════════════ LAYOUT: euclidean_division ═══════════════════════

def layout_euclidean_division(dividend: int, divisor: int, quotient: int, remainder: int,
                               dividend_label: str, divisor_label: str,
                               quotient_label: str, remainder_label: str,
                               subtractions: Optional[list[dict]] = None) -> list[dict]:
    x_line, y_top, y_bottom = 180, 40, 220
    steps = [{"label": str(dividend), "ops": [
        {"type": "line", "from": [x_line, y_top], "to": [x_line, y_bottom], "color": "text", "lineWidth": 2},
        {"type": "text", "text": dividend_label, "at": [40, 50], "color": "text"},
        {"type": "text", "text": str(dividend), "at": [55, 75], "color": "secondary"},
        {"type": "text", "text": divisor_label, "at": [200, 50], "color": "text"},
        {"type": "text", "text": str(divisor), "at": [225, 75], "color": "primary"},
        {"type": "line", "from": [x_line, 90], "to": [260, 90], "color": "text", "lineWidth": 1.5},
    ]}]

    steps.append({"label": str(quotient), "ops": [
        {"type": "text", "text": str(quotient), "at": [225, 115], "color": "accent"},
        {"type": "text", "text": quotient_label, "at": [185, 135], "color": "text"},
    ]})

    left_y = 115
    if subtractions:
        for sub in subtractions:
            # BUGFIX: same off-by-one pattern as layout_symbolic_formula —
            # this used to advance left_y past BOTH lines of a subtraction
            # step (the "-N" line and the running-remainder line below it)
            # and only then check the resulting position, so it rejected
            # subtraction sequences whose every actually-used y was fine
            # (e.g. 2 steps landing at y=115/155/180/220, all comfortably
            # under the 240 ceiling) purely because the NEXT unused
            # position would have overflowed. Checking right before each
            # position is actually used means only a genuinely-off-canvas
            # line ever raises.
            if left_y > 240:
                raise ValueError("too many subtraction steps for canvas height")
            steps.append({"label": f"-{sub['subtract']}", "ops": [
                {"type": "text", "text": f"-{sub['subtract']}", "at": [45, left_y], "color": "text"},
                {"type": "line", "from": [40, left_y + 13], "to": [100, left_y + 13],
                 "color": "text", "lineWidth": 1.5},
            ]})
            left_y += 40
            if left_y > 240:
                raise ValueError("too many subtraction steps for canvas height")
            steps.append({"label": str(sub["remainder_after"]), "ops": [
                {"type": "text", "text": str(sub["remainder_after"]), "at": [55, left_y], "color": "text"},
            ]})
            left_y += 25

    steps.append({"label": f"{remainder_label} = {remainder}", "ops": [
        {"type": "text", "text": str(remainder), "at": [90, min(left_y + 15, 230)], "color": "hi"},
        {"type": "text", "text": remainder_label, "at": [80, min(left_y + 35, 250)], "color": "text"},
    ]})

    steps.append({"label": f"{dividend} = {divisor} × {quotient} + {remainder}", "ops": [
        {"type": "text", "text": f"{dividend} = {divisor} × {quotient} + {remainder}",
         "at": [55, 275], "color": "text"},
    ]})
    return steps


# ═══════════════════════ LAYOUT: factor_tree ═══════════════════════

def layout_factor_tree(tree: dict) -> list[dict]:
    """
    tree: {"value": int, "prime": bool, "left"?: tree, "right"?: tree}
    Layout par algorithme récursif standard d'arbre binaire (x = position
    séquentielle des feuilles, y = profondeur) — se généralise à n'importe
    quelle forme d'arbre sans jamais coder une position en dur.
    """
    LEAF_GAP_X, LEVEL_GAP_Y, Y0 = 55, 55, 35
    positions: dict[int, tuple[float, float]] = {}
    next_leaf_x = [40]

    def assign_x(node, depth):
        node_id = id(node)
        left, right = node.get("left"), node.get("right")
        if not left and not right:
            x = next_leaf_x[0]
            next_leaf_x[0] += LEAF_GAP_X
        else:
            xs = [assign_x(c, depth + 1) for c in (left, right) if c]
            x = sum(xs) / len(xs)
        positions[node_id] = (x, Y0 + depth * LEVEL_GAP_Y)
        return x

    assign_x(tree, 0)
    if next_leaf_x[0] > W - 20:
        raise ValueError("factor tree too wide for canvas — number has too many prime factors")

    def depth_of(node):
        children = [c for c in (node.get("left"), node.get("right")) if c]
        return 0 if not children else 1 + max(depth_of(c) for c in children)

    if Y0 + depth_of(tree) * LEVEL_GAP_Y > H - 30:
        raise ValueError("factor tree too deep for canvas")

    steps = []

    def collect(node, depth):
        x, y = positions[id(node)]
        color = "hi" if node.get("prime") else ("text" if depth == 0 else "secondary")
        steps.append({"label": str(node["value"]), "ops": [
            {"type": "text", "text": str(node["value"]), "at": [x - 6, y], "color": color}
        ]})
        for child in (node.get("left"), node.get("right")):
            if child:
                cx, cy = positions[id(child)]
                steps.append({"label": "", "ops": [
                    {"type": "line", "from": [x, y + 5], "to": [cx, cy - 10], "color": "aux"}
                ]})
                collect(child, depth + 1)

    collect(tree, 0)

    def primes_of(node, acc):
        if node.get("prime"):
            acc.append(node["value"])
        for c in (node.get("left"), node.get("right")):
            if c:
                primes_of(c, acc)
        return acc

    counts = Counter(sorted(primes_of(tree, [])))
    parts = [f"{p}{_superscript(n)}" if n > 1 else str(p) for p, n in sorted(counts.items())]
    factorization = f"{tree['value']} = " + " × ".join(parts)
    steps.append({"label": factorization, "ops": [
        {"type": "text", "text": factorization,
         "at": [40, Y0 + depth_of(tree) * LEVEL_GAP_Y + 30], "color": "text"}
    ]})
    return steps


# ═══════════════════════ LAYOUT: mirror_symmetry ═══════════════════════

def layout_mirror_symmetry(variant: str, axis_x: int = 240, **p) -> list[dict]:
    steps = [{"label": "(d)", "ops": [
        {"type": "line", "from": [axis_x, 20], "to": [axis_x, 280], "color": "accent", "lineWidth": 2.5},
        {"type": "text", "text": "(d)", "at": [axis_x + 8, 18], "color": "accent"},
    ]}]

    if variant == "point":
        px, py = p.get("point_x", 150), p.get("point_y", 150)
        p_label = p.get("point_label", "P")
        image_label = p.get("image_label", f"{p_label}'")
        steps.append({"label": p_label, "ops": [
            {"type": "dot", "at": [px, py], "color": "primary", "radius": 5},
            {"type": "text", "text": p_label, "at": [px - 12, py + 18], "color": "primary"},
        ]})
        if abs(px - axis_x) < 1:
            return steps  # point sur l'axe -> son image est lui-même
        img_x = 2 * axis_x - px
        steps.append({"label": f"{p_label} → {image_label}", "ops": [
            {"type": "line", "from": [px, py], "to": [img_x, py], "color": "aux",
             "lineWidth": 1.5, "dash": [5, 4]},
            {"type": "dot", "at": [axis_x, py], "color": "hi", "radius": 4},
            {"type": "text", "text": "H", "at": [axis_x - 12, py + 18], "color": "hi"},
        ]})
        steps.append({"label": image_label, "ops": [
            {"type": "dot", "at": [img_x, py], "color": "secondary", "radius": 5},
            {"type": "text", "text": image_label, "at": [img_x + 10, py + 18], "color": "secondary"},
        ]})
        return steps

    if variant == "circle":
        cx, cy, r = p.get("center_x", 150), p.get("center_y", 150), p.get("radius", 50)
        c_label = p.get("center_label", "C")
        image_label = p.get("image_label", f"{c_label}'")
        steps.append({"label": c_label, "ops": [
            {"type": "arc", "cx": cx, "cy": cy, "r": r, "startAngle": 0, "endAngle": 6.283, "color": "primary"},
            {"type": "dot", "at": [cx, cy], "color": "hi", "radius": 4},
            {"type": "text", "text": c_label, "at": [cx - 12, cy + 18], "color": "hi"},
        ]})
        if abs(cx - axis_x) < 1:
            return steps
        img_cx = 2 * axis_x - cx
        steps.append({"label": image_label, "ops": [
            {"type": "arc", "cx": img_cx, "cy": cy, "r": r, "startAngle": 0, "endAngle": 6.283, "color": "secondary"},
            {"type": "dot", "at": [img_cx, cy], "color": "hi", "radius": 4},
            {"type": "text", "text": image_label, "at": [img_cx + 10, cy + 18], "color": "hi"},
        ]})
        return steps

    if variant == "line_special_cases":
        case = p.get("case", "parallel")
        if case == "parallel":
            lx = p.get("line_x", 140)
            l_label, l2_label = p.get("line_label", "ℓ"), p.get("image_label", "ℓ'")
            img_x = 2 * axis_x - lx
            steps.append({"label": l_label, "ops": [
                {"type": "line", "from": [lx, 60], "to": [lx, 240], "color": "primary", "lineWidth": 2.5},
                {"type": "text", "text": l_label, "at": [lx - 12, 58], "color": "primary"},
            ]})
            steps.append({"label": l2_label, "ops": [
                {"type": "line", "from": [img_x, 60], "to": [img_x, 240], "color": "secondary", "lineWidth": 2.5},
                {"type": "text", "text": l2_label, "at": [img_x + 8, 58], "color": "secondary"},
            ]})
            return steps
        if case == "perpendicular":
            l_label = p.get("line_label", "ℓ")
            steps.append({"label": l_label, "ops": [
                {"type": "line", "from": [axis_x, 60], "to": [axis_x, 240], "color": "primary", "lineWidth": 2.5},
                {"type": "text", "text": l_label, "at": [axis_x + 10, 58], "color": "primary"},
            ]})
            return steps
        raise ValueError(f"unknown line_special_cases case: {case!r}")

    if variant == "angle":
        ox, oy = p.get("vertex_x", 150), p.get("vertex_y", 200)
        o_label = p.get("vertex_label", "O")
        img_ox = 2 * axis_x - ox
        steps.append({"label": o_label, "ops": [
            {"type": "line", "from": [ox, oy], "to": [ox, oy - 80], "color": "primary", "lineWidth": 2.5},
            {"type": "line", "from": [ox, oy], "to": [ox - 60, oy + 40], "color": "primary", "lineWidth": 2.5},
            {"type": "dot", "at": [ox, oy], "color": "hi", "radius": 4},
            {"type": "text", "text": o_label, "at": [ox + 8, oy + 10], "color": "hi"},
        ]})
        steps.append({"label": f"{o_label}'", "ops": [
            {"type": "line", "from": [img_ox, oy], "to": [img_ox, oy - 80], "color": "secondary", "lineWidth": 2.5},
            {"type": "line", "from": [img_ox, oy], "to": [img_ox + 60, oy + 40], "color": "secondary", "lineWidth": 2.5},
            {"type": "dot", "at": [img_ox, oy], "color": "hi", "radius": 4},
            {"type": "text", "text": f"{o_label}'", "at": [img_ox + 8, oy + 10], "color": "hi"},
        ]})
        return steps

    raise ValueError(f"unknown mirror_symmetry variant: {variant!r}")


# ═══════════════════════ LAYOUT: compass_construction ═══════════════════════

def layout_compass_construction(variant: str = "perpendicular_bisector", **p) -> list[dict]:
    if variant == "angle_bisector":
        # v28: angle bisector construction (arc from vertex cutting both
        # rays at D/E, then two equal-radius arcs from D and E
        # intersecting at F, then ray OF). Every point below is computed
        # from the actual ray angle via real trig — never a hallucinated
        # pixel — which is what this variant exists to guarantee, unlike
        # the freeform_svg fallback that used to produce this drawing.
        ox, oy = p.get("vertex_x", 170), p.get("vertex_y", 220)
        o_label = p.get("vertex_label", "O")
        a_label = p.get("a_label", "A")
        b_label = p.get("b_label", "B")
        d_label = p.get("d_label", "D")
        e_label = p.get("e_label", "E")
        f_label = p.get("f_label", "F")
        angle_deg = p.get("angle_degrees", 60)
        r_ray = 150

        def _ray_endpoint(angle_from_ox):
            rad = math.radians(angle_from_ox)
            return ox + r_ray * math.cos(rad), oy - r_ray * math.sin(rad)

        ax, ay = _ray_endpoint(0)
        bx, by = _ray_endpoint(angle_deg)
        _check_bounds(ax, ay, context="angle_bisector ray a")
        _check_bounds(bx, by, context="angle_bisector ray b")

        steps = [{"label": o_label, "ops": [
            {"type": "dot", "at": [ox, oy], "color": "hi", "radius": 4},
            {"type": "text", "text": o_label, "at": [ox - 14, oy + 16], "color": "hi"},
        ]}]
        steps.append({"label": a_label, "ops": [
            {"type": "line", "from": [ox, oy], "to": [ax, ay], "color": "primary", "lineWidth": 2.5},
            {"type": "text", "text": a_label, "at": [ax + 6, ay], "color": "primary"},
        ]})
        steps.append({"label": b_label, "ops": [
            {"type": "line", "from": [ox, oy], "to": [bx, by], "color": "secondary", "lineWidth": 2.5},
            {"type": "text", "text": b_label, "at": [bx + 6, by - 6], "color": "secondary"},
        ]})

        # Step 1: arc from O, radius r1, cutting OA at D and OB at E —
        # both computed from the real ray angles (0 and angle_deg), not
        # guessed.
        r1 = 70
        rad_b = math.radians(angle_deg)
        dx, dy = ox + r1, oy  # D sits on OA (angle 0), distance r1 from O
        ex, ey = ox + r1 * math.cos(rad_b), oy - r1 * math.sin(rad_b)  # E on OB
        _check_bounds(dx, dy, margin=5, context="angle_bisector point D")
        _check_bounds(ex, ey, margin=5, context="angle_bisector point E")
        steps.append({"label": f"قوس من {o_label}", "ops": [
            {"type": "arc", "cx": ox, "cy": oy, "r": r1,
             "startAngle": -rad_b, "endAngle": 0, "color": "accent"},
            {"type": "dot", "at": [dx, dy], "color": "hi", "radius": 3},
            {"type": "text", "text": d_label, "at": [dx + 6, dy + 12], "color": "accent"},
            {"type": "dot", "at": [ex, ey], "color": "hi", "radius": 3},
            {"type": "text", "text": e_label, "at": [ex + 6, ey - 10], "color": "accent"},
        ]})

        # Step 2 & 3: two equal-radius arcs from D and E, intersecting at
        # F. F is placed deterministically ON the true bisector
        # (angle_deg / 2) — same direction principle as angle_pair's
        # bisector label placement — but at the ACTUAL distance where two
        # radius-r2 circles centered at D and E really intersect, computed
        # via real circle-circle intersection geometry, not a guessed
        # offset (r1 + 25) that had no relation to r2 or angle_deg.
        #
        # BUGFIX: the previous (r1 + 25) offset placed F at an arbitrary
        # spot; separately, the two arcs' angular ranges were a heuristic
        # (pi +- bis_rad +- 0.5) that didn't derive from real geometry
        # either. Checked numerically for the default angle_degrees=60:
        # neither arc actually passed anywhere near the old F — the D-arc
        # missed by ~46 deg, the E-arc by ~197 deg — so the two "crossing"
        # arcs and the F marker visibly didn't meet. Fixed below by (1)
        # solving for the true intersection distance along the bisector,
        # and (2) computing each arc's center angle from the REAL
        # direction from D (resp. E) to that true F, via the same
        # flip-convention atan2 + negation pattern already used elsewhere
        # in this file (see _interior_angle_arc's bugfix note above for
        # why the negation is required to match the renderer).
        bis_rad = math.radians(angle_deg / 2)
        half_de = r1 * math.sin(bis_rad)  # half the straight-line distance D–E
        r2 = max(r1 * 0.85, half_de + 10)  # must exceed half_de for the two
        # circles to actually intersect; +10 keeps a safety margin for
        # wide angle_degrees values where half_de approaches r1.
        om = r1 * math.cos(bis_rad)  # O -> foot of perpendicular from O onto DE, along the bisector
        mf = math.sqrt(max(r2 * r2 - half_de * half_de, 0.0))  # that foot -> true intersection point
        of = om + mf
        fx = ox + of * math.cos(bis_rad)
        fy = oy - of * math.sin(bis_rad)
        _check_bounds(fx, fy, margin=5, context="angle_bisector point F")

        def _flip_angle(vx: float, vy: float) -> float:
            """Angle (in this file's flip convention) of vector (vx,vy)."""
            return math.atan2(-vy, vx)

        half_width = 0.4  # radians of visible arc drawn on each side of F
        dir_d_to_f = -_flip_angle(fx - dx, fy - dy)   # negated: renderer convention
        dir_e_to_f = -_flip_angle(fx - ex, fy - ey)

        steps.append({"label": f"قوس من {d_label}", "ops": [
            {"type": "arc", "cx": dx, "cy": dy, "r": r2,
             "startAngle": dir_d_to_f - half_width, "endAngle": dir_d_to_f + half_width,
             "color": "aux"},
        ]})
        steps.append({"label": f"قوس من {e_label}", "ops": [
            {"type": "arc", "cx": ex, "cy": ey, "r": r2,
             "startAngle": dir_e_to_f - half_width, "endAngle": dir_e_to_f + half_width,
             "color": "aux"},
        ]})
        steps.append({"label": f"[{o_label}{f_label})", "ops": [
            {"type": "line", "from": [ox, oy], "to": [fx, fy], "color": "hi", "lineWidth": 2.5},
            {"type": "dot", "at": [fx, fy], "color": "hi", "radius": 4},
            {"type": "text", "text": f_label, "at": [fx + 6, fy - 8], "color": "hi"},
        ]})
        return steps

    if variant == "perpendicular_on_line":
        # Construction: point A lies ON line (d). Mark two points P1/P2 on
        # the line, equidistant from A; two equal-radius arcs from P1/P2
        # meet at M; the line AM is the perpendicular. Because P1/P2 are
        # symmetric about A along the line, M lands exactly above A by
        # construction (h below), never approximated — same "derive, don't
        # hallucinate" discipline as perpendicular_bisector's midpoint.
        line_x1, line_y = 60, 200
        line_x2 = W - 60
        line_label = p.get("line_label", "(d)")
        a_label = p.get("point_label", p.get("a_label", "A"))
        m_label = p.get("m_label", "M")
        # BUGFIX: the two points where the compass arcs cross (d) were
        # drawn as silent dots — no label param existed for them at all,
        # yet [TEACHER] text for this construction routinely names them
        # (e.g. "M و N") before naming the apex separately. Since the
        # already-existing m_label defaults to "M", default point1/point2
        # to something else ("P1"/"P2") to avoid a silent letter clash
        # when the model (correctly) also sets m_label to something like
        # "P" for the apex per the updated params_schema guidance below.
        p1_label = p.get("point1_label", "P1")
        p2_label = p.get("point2_label", "P2")
        method = p.get("method", "compass")

        ax = p.get("a_x", (line_x1 + line_x2) / 2)
        ay = line_y
        _check_bounds(ax, ay, margin=40, context="perpendicular_on_line point A")

        steps = [{"label": line_label, "ops": [
            {"type": "line", "from": [line_x1, line_y], "to": [line_x2, line_y],
             "color": "primary", "lineWidth": 2},
            {"type": "text", "text": line_label, "at": [line_x1 - 10, line_y + 22], "color": "primary"},
        ]}]
        steps.append({"label": a_label, "ops": [
            {"type": "dot", "at": [ax, ay], "color": "hi", "radius": 4},
            {"type": "text", "text": a_label, "at": [ax - 5, ay + 22], "color": "primary"},
        ]})

        # method="direct": skip the compass arcs entirely — just the line,
        # point A, and a straight perpendicular through it with a
        # right-angle mark. No P1/P2/M-via-arcs construction at all.
        if method == "direct":
            direct_h = 90
            mx, my = ax, ay - direct_h
            _check_bounds(mx, my, margin=10, context="perpendicular_on_line point M (direct)")
            steps.append({"label": m_label, "ops": [
                {"type": "line", "from": [ax, ay], "to": [mx, my], "color": "accent", "lineWidth": 2.5},
                {"type": "text", "text": m_label, "at": [mx + 6, my + 10], "color": "hi"},
                {"type": "line", "from": [ax - 12, ay], "to": [ax - 12, ay - 12], "color": "accent", "lineWidth": 1.5},
                {"type": "line", "from": [ax - 12, ay - 12], "to": [ax, ay - 12], "color": "accent", "lineWidth": 1.5},
            ]})
            return steps

        d = min(ax - line_x1, line_x2 - ax, 60) * 0.7
        if d < 20:
            raise ValueError("perpendicular_on_line: point A too close to line end")
        p1x, p2x = ax - d, ax + d

        r = d * 1.6  # > d so the two arcs actually intersect above the line
        h = math.sqrt(max(r * r - d * d, 0.0))
        mx, my = ax, ay - h
        _check_bounds(mx, my, margin=10, context="perpendicular_on_line point M")

        steps.append({"label": f"{p1_label}, {p2_label}", "ops": [
            {"type": "dot", "at": [p1x, line_y], "color": "text", "radius": 3},
            {"type": "text", "text": p1_label, "at": [p1x - 8, line_y + 18], "color": "text"},
            {"type": "dot", "at": [p2x, line_y], "color": "text", "radius": 3},
            {"type": "text", "text": p2_label, "at": [p2x + 4, line_y + 18], "color": "text"},
            {"type": "arc", "cx": p1x, "cy": line_y, "r": r,
             "startAngle": math.radians(35), "endAngle": math.radians(75), "color": "secondary"},
        ]})
        steps.append({"label": "قوس ثانٍ", "ops": [
            {"type": "arc", "cx": p2x, "cy": line_y, "r": r,
             "startAngle": math.radians(105), "endAngle": math.radians(145), "color": "secondary"},
        ]})
        steps.append({"label": m_label, "ops": [
            {"type": "dot", "at": [mx, my], "color": "hi", "radius": 4},
            {"type": "text", "text": m_label, "at": [mx + 6, my - 6], "color": "hi"},
        ]})
        steps.append({"label": f"({a_label}{m_label})", "ops": [
            {"type": "line", "from": [ax, ay], "to": [mx, my], "color": "accent", "lineWidth": 2.5},
            {"type": "line", "from": [ax - 12, ay], "to": [ax - 12, ay - 12], "color": "accent", "lineWidth": 1.5},
            {"type": "line", "from": [ax - 12, ay - 12], "to": [ax, ay - 12], "color": "accent", "lineWidth": 1.5},
        ]})
        return steps

    if variant == "perpendicular_external_point":
        # Construction: point A does NOT lie on line (d). Arc from A cuts
        # the line at P1/P2; two equal-radius arcs from P1/P2 meet at M on
        # the far side; line AM crosses the line at the perpendicular
        # foot H. P1/P2 are symmetric about the foot of A on the line, so
        # M lands exactly on the vertical through A by construction —
        # same derivation principle as perpendicular_on_line above.
        line_x1, line_y = 60, 180
        line_x2 = W - 60
        line_label = p.get("line_label", "(d)")
        a_label = p.get("point_label", p.get("a_label", "A"))
        m_label = p.get("m_label", "M")
        h_label = p.get("foot_label", "H")
        # BUGFIX: same gap as perpendicular_on_line above — the two
        # points where the arc from A crosses (d) had no label param and
        # were never drawn with text, even though [TEACHER] text for this
        # construction commonly names them before naming the apex/foot.
        p1_label = p.get("point1_label", "P1")
        p2_label = p.get("point2_label", "P2")
        method = p.get("method", "compass")

        ax = p.get("a_x", (line_x1 + line_x2) / 2)
        ay = 60  # fixed distance above the line, well within canvas
        _check_bounds(ax, ay, margin=15, context="perpendicular_external_point point A")

        steps = [{"label": line_label, "ops": [
            {"type": "line", "from": [line_x1, line_y], "to": [line_x2, line_y],
             "color": "primary", "lineWidth": 2},
            {"type": "text", "text": line_label, "at": [line_x1 - 10, line_y + 22], "color": "primary"},
        ]}]
        steps.append({"label": a_label, "ops": [
            {"type": "dot", "at": [ax, ay], "color": "hi", "radius": 4},
            {"type": "text", "text": a_label, "at": [ax - 5, ay - 16], "color": "primary"},
        ]})

        # method="direct": skip the compass arcs entirely — just the line,
        # point A, and a straight perpendicular dropped to its foot H,
        # with a right-angle mark. No P1/P2/M-via-arcs construction at all.
        if method == "direct":
            hx, hy = ax, line_y
            _check_bounds(hx, hy, margin=10, context="perpendicular_external_point point H (direct)")
            steps.append({"label": h_label, "ops": [
                {"type": "line", "from": [ax, ay], "to": [hx, hy], "color": "accent", "lineWidth": 2.5},
                {"type": "dot", "at": [hx, hy], "color": "hi", "radius": 3},
                {"type": "text", "text": h_label, "at": [hx + 6, hy - 4], "color": "hi"},
                {"type": "line", "from": [ax - 12, line_y], "to": [ax - 12, line_y - 12], "color": "accent", "lineWidth": 1.5},
                {"type": "line", "from": [ax - 12, line_y - 12], "to": [ax, line_y - 12], "color": "accent", "lineWidth": 1.5},
            ]})
            return steps

        vert_dist = line_y - ay
        r1 = vert_dist * 1.4  # > vert_dist so the arc actually crosses the line twice
        d = math.sqrt(max(r1 * r1 - vert_dist * vert_dist, 0.0))
        if d < 20:
            raise ValueError("perpendicular_external_point: A too close to line for a clean crossing")
        p1x, p2x = ax - d, ax + d
        _check_bounds(p1x, line_y, margin=5, context="perpendicular_external_point crossing P1")
        _check_bounds(p2x, line_y, margin=5, context="perpendicular_external_point crossing P2")

        steps.append({"label": f"{p1_label}, {p2_label}", "ops": [
            {"type": "arc", "cx": ax, "cy": ay, "r": r1,
             "startAngle": math.radians(240), "endAngle": math.radians(300), "color": "secondary"},
            {"type": "dot", "at": [p1x, line_y], "color": "hi", "radius": 3},
            {"type": "text", "text": p1_label, "at": [p1x - 8, line_y + 18], "color": "hi"},
            {"type": "dot", "at": [p2x, line_y], "color": "hi", "radius": 3},
            {"type": "text", "text": p2_label, "at": [p2x + 4, line_y + 18], "color": "hi"},
        ]})

        # BUGFIX: r2 used to be an arbitrary multiplier of d (r2 = d*1.3),
        # which let M's height below the line (h = sqrt(r2^2 - d^2)) grow
        # unboundedly for larger d — confirmed to push M off-canvas
        # (position (240,342) vs H=300) with this function's own default
        # A/line placement. Solve the other way instead: pick a target
        # height that's guaranteed to fit below the line, then derive r2
        # from it — so M's position is bounds-safe BY CONSTRUCTION, not by
        # tuning a constant against one default case.
        target_h = min(0.75 * d, (H - 15) - line_y)
        if target_h < 15:
            raise ValueError("perpendicular_external_point: not enough canvas room below the line for M")
        r2 = math.hypot(d, target_h)  # > d automatically, so the arcs do intersect
        mx, my = ax, line_y + target_h
        _check_bounds(mx, my, margin=10, context="perpendicular_external_point point M")

        steps.append({"label": "قوسان متقاطعان", "ops": [
            {"type": "arc", "cx": p1x, "cy": line_y, "r": r2,
             "startAngle": math.radians(-75), "endAngle": math.radians(-35), "color": "aux"},
            {"type": "arc", "cx": p2x, "cy": line_y, "r": r2,
             "startAngle": math.radians(215), "endAngle": math.radians(255), "color": "aux"},
        ]})
        steps.append({"label": m_label, "ops": [
            {"type": "dot", "at": [mx, my], "color": "hi", "radius": 4},
            {"type": "text", "text": m_label, "at": [mx + 6, my + 14], "color": "hi"},
        ]})
        steps.append({"label": f"({a_label}{m_label})", "ops": [
            {"type": "line", "from": [ax, ay], "to": [mx, my], "color": "accent", "lineWidth": 2.5},
            {"type": "dot", "at": [ax, line_y], "color": "hi", "radius": 3},
            {"type": "text", "text": h_label, "at": [ax + 6, line_y - 4], "color": "hi"},
            {"type": "line", "from": [ax - 12, line_y], "to": [ax - 12, line_y - 12], "color": "accent", "lineWidth": 1.5},
            {"type": "line", "from": [ax - 12, line_y - 12], "to": [ax, line_y - 12], "color": "accent", "lineWidth": 1.5},
        ]})
        return steps

    if variant != "perpendicular_bisector":
        raise ValueError(f"unknown compass_construction variant: {variant!r}")

    ax, ay = p.get("a", [120, 150])
    bx, by = p.get("b", [360, 150])
    a_label, b_label = p.get("a_label", "A"), p.get("b_label", "B")
    m_label = p.get("m_label", "M")

    # BUGFIX: a/b are no longer advertised to the LLM (see REPRESENTATIONS
    # catalogue note above), but this function still accepts them as an
    # internal override — previously with ZERO bounds validation, so a
    # caller (or a model ignoring the menu) could pass e.g. a=[5000,5000]
    # and get a silently off-canvas drawing. _check_bounds() now guards
    # both endpoints explicitly, same as layout_number_line already did
    # for its points.
    _check_bounds(ax, ay, context="compass_construction point a")
    _check_bounds(bx, by, context="compass_construction point b")

    steps = [{"label": f"[{a_label}{b_label}]", "ops": [
        {"type": "line", "from": [ax, ay], "to": [bx, by], "color": "primary", "lineWidth": 2.5},
        {"type": "dot", "at": [ax, ay], "color": "hi", "radius": 4},
        {"type": "text", "text": a_label, "at": [ax - 14, ay - 6], "color": "primary"},
        {"type": "dot", "at": [bx, by], "color": "hi", "radius": 4},
        {"type": "text", "text": b_label, "at": [bx + 8, by - 6], "color": "primary"},
    ]}]

    dx, dy = bx - ax, by - ay
    dist = math.hypot(dx, dy)
    if dist < 1:
        raise ValueError("points A and B coincide")
    base_angle = math.atan2(dy, dx)
    r = dist * 0.75

    # v30: draw SHORT crossing marks at the two true circle-circle
    # intersection points, instead of one continuous arc sweeping through
    # the whole angular range between them. A continuous sweep from
    # base_angle-θ to base_angle+θ passes through angle 0 (straight at
    # the other point), which is what produced the tall "vesica"/lens
    # belly bulging toward the other point — geometrically correct but
    # visually oversized, especially for long segments. A real compass
    # diagram conventionally shows two small crossing hash marks at the
    # intersection points, not the full lens. exact_angle is still
    # derived from the real right triangle formed by the center, the
    # midpoint M, and the true intersection point (adjacent = dist/2,
    # hypotenuse = r) — same "derive, don't guess" discipline as before —
    # so the marks are centered exactly where the circles truly cross.
    # mark_half_width is a small, fixed angular length for each hash
    # mark, independent of segment length (so the marks don't grow with
    # dist the way the old continuous-sweep version did).
    exact_angle = math.acos((dist / 2) / r)

    def _arc_extreme(cx: float, cy: float, angle: float) -> tuple[float, float]:
        return cx + r * math.cos(angle), cy + r * math.sin(angle)

    # BUGFIX: a fixed mark_half_width (0.22 rad) is fine in general, but
    # for segments whose true intersection point already sits close to
    # the canvas edge (e.g. this function's own default a/b, where the
    # unpadded intersection lands at y≈284 of H=300) even a small further
    # sweep pushes an endpoint off-canvas. Rather than reject that
    # perfectly valid default construction, shrink the mark per-point:
    # try decreasing widths and keep the largest one whose both ends
    # still fit, only raising if even the smallest candidate doesn't fit
    # (i.e. the true intersection itself has no room at all).
    def _fit_mark_width(cx: float, cy: float, angle: float) -> float:
        for w in (0.22, 0.16, 0.12, 0.09, 0.07, 0.05):
            if all(5 <= (ex := _arc_extreme(cx, cy, angle + sign * w))[0] <= W - 5
                   and 5 <= ex[1] <= H - 5 for sign in (-1, 1)):
                return w
        raise ValueError(
            "compass_construction: no room to draw a crossing mark at the "
            "bisector intersection point — A/B are too close to the canvas edge"
        )

    a_top_angle = base_angle + exact_angle
    a_bot_angle = base_angle - exact_angle
    b_top_angle = base_angle + math.pi - exact_angle
    b_bot_angle = base_angle + math.pi + exact_angle

    w_a_top = _fit_mark_width(ax, ay, a_top_angle)
    w_a_bot = _fit_mark_width(ax, ay, a_bot_angle)
    w_b_top = _fit_mark_width(bx, by, b_top_angle)
    w_b_bot = _fit_mark_width(bx, by, b_bot_angle)

    steps.append({"label": f"قوس من {a_label}", "ops": [
        {"type": "arc", "cx": ax, "cy": ay, "r": r,
         "startAngle": a_top_angle - w_a_top, "endAngle": a_top_angle + w_a_top, "color": "secondary"},
        {"type": "arc", "cx": ax, "cy": ay, "r": r,
         "startAngle": a_bot_angle - w_a_bot, "endAngle": a_bot_angle + w_a_bot, "color": "secondary"},
    ]})
    steps.append({"label": f"قوس من {b_label}", "ops": [
        {"type": "arc", "cx": bx, "cy": by, "r": r,
         "startAngle": b_top_angle - w_b_top, "endAngle": b_top_angle + w_b_top, "color": "aux"},
        {"type": "arc", "cx": bx, "cy": by, "r": r,
         "startAngle": b_bot_angle - w_b_bot, "endAngle": b_bot_angle + w_b_bot, "color": "aux"},
    ]})



    mx, my = (ax + bx) / 2, (ay + by) / 2
    perp_angle = base_angle + math.pi / 2
    half_len = max(dist * 0.6, 100)
    p1 = (mx + half_len * math.cos(perp_angle), my + half_len * math.sin(perp_angle))
    p2 = (mx - half_len * math.cos(perp_angle), my - half_len * math.sin(perp_angle))

    # BUGFIX: p1/p2 are DERIVED from a/b (via half_len), so even in-bounds
    # a/b can push the perpendicular bisector's endpoints off-canvas (e.g.
    # a/b far apart and near a canvas edge). Margin=5 here (not the default
    # 15) is deliberate: with the module's own default example
    # (a=[120,150], b=[360,150]) the endpoints legitimately land at
    # (240,294) and (240,6) — a few px from the canvas edge but still
    # fully visible — so a stricter margin would reject a perfectly valid
    # drawing. The point of this check is to catch genuinely absurd/
    # hallucinated values (e.g. a=[5000,5000]), not to forbid a
    # construction line from approaching the edge.
    _check_bounds(*p1, margin=5, context="compass_construction bisector endpoint")
    _check_bounds(*p2, margin=5, context="compass_construction bisector endpoint")

    steps.append({"label": "(d)", "ops": [
        {"type": "line", "from": list(p1), "to": list(p2), "color": "accent", "lineWidth": 2.5},
        {"type": "text", "text": "(d)", "at": [p1[0] + 8, p1[1]], "color": "accent"},
    ]})
    steps.append({"label": m_label, "ops": [
        {"type": "dot", "at": [mx, my], "color": "hi", "radius": 5},
        {"type": "text", "text": m_label, "at": [mx - 12, my + 18], "color": "hi"},
    ]})

    # v31: optional equidistance-property demonstration. Previously this
    # variant only ever drew the CONSTRUCTION (A, B, arcs, bisector, M) —
    # nothing here could show the actual PROPERTY lessons teach next
    # ("any point P on the bisector has PA = PB"), so a property-focused
    # turn had no real option but to reuse the plain construction diagram
    # with nothing new added, even though the content calls for a demo
    # point P and its distances to A/B. p_label is deliberately a
    # SEPARATE param from m_label (never silently reuses/overloads M) —
    # P is placed away from M along (d) specifically so it reads as "an
    # arbitrary point," not "the midpoint again."
    p_label = p.get("demo_point_label") or p.get("p_label")
    if p_label:
        # BUGFIX: t=0.85 previously placed P almost exactly where the
        # compass arc crossing-marks sit (both top AND bottom — arcs are
        # drawn symmetrically on both ends of the bisector), since
        # h_arc = √(r²-(dist/2)²) ≈ 0.56·dist and half_len ≈ 0.6·dist end
        # up nearly equal for this function's own r=dist*0.75. That
        # caused P/its label to visually fuse with the arc "X" and the
        # (d) label — on either side, not just one — regardless of which
        # direction was picked. Bound P's distance from M to stay clearly
        # inside that zone instead of guessing a fraction that happens to
        # avoid it for one geometry but not another.
        h_arc = math.sqrt(max(r * r - (dist / 2) ** 2, 0))
        p_dist = min(0.55 * half_len, 0.6 * h_arc) if h_arc > 0 else 0.5 * half_len
        cand1 = (mx + p_dist * math.cos(perp_angle), my + p_dist * math.sin(perp_angle))
        cand2 = (mx - p_dist * math.cos(perp_angle), my - p_dist * math.sin(perp_angle))
        def _in_bounds(pt, margin=20):
            return margin <= pt[0] <= W - margin and margin <= pt[1] <= H - margin
        if _in_bounds(cand1):
            px_, py_ = cand1
        elif _in_bounds(cand2):
            px_, py_ = cand2
        else:
            # neither extreme fits — fall back to a shorter, guaranteed-
            # safe distance from M rather than failing the whole widget
            # over an optional demonstration point.
            t = 0.4
            px_, py_ = (mx + t * half_len * math.cos(perp_angle), my + t * half_len * math.sin(perp_angle))
            if not _in_bounds((px_, py_)):
                px_, py_ = (mx - t * half_len * math.cos(perp_angle), my - t * half_len * math.sin(perp_angle))
        _check_bounds(px_, py_, margin=15, context="compass_construction demo point P")
        steps.append({"label": p_label, "ops": [
            {"type": "dot", "at": [px_, py_], "color": "hi", "radius": 4},
            {"type": "text", "text": p_label, "at": [px_ + 8, py_ - 6], "color": "hi"},
        ]})
        steps.append({"label": f"{p_label}{a_label}", "ops": [
            {"type": "line", "from": [px_, py_], "to": [ax, ay], "color": "primary",
             "lineWidth": 1.5, "dash": [5, 4]},
        ]})
        steps.append({"label": f"{p_label}{b_label}", "ops": [
            {"type": "line", "from": [px_, py_], "to": [bx, by], "color": "primary",
             "lineWidth": 1.5, "dash": [5, 4]},
        ]})
        steps.append({"label": f"{p_label}{a_label}={p_label}{b_label}", "ops": [
            {"type": "text", "text": f"{p_label}{a_label} = {p_label}{b_label}",
             "at": [min(px_, mx) - 10, max(py_, my) + 22], "color": "hi"},
        ]})

    return steps


# ═══════════════════════ LAYOUT: angle_pair ═══════════════════════

def _widehat_ops(cx: float, top_y: float, text: str, color: str) -> list[dict]:
    """
    Renders an angle name like \\widehat{AOB} using ONLY this file's
    existing vector op primitives — a small `arc` for the cap plus a
    `text` op for the letters. Never a Unicode stand-in character (‸,
    ⌢, ˆ, ^…) and never a rotated ASCII character.

    Why this matters: the SVG widget has no LaTeX engine, so \\widehat{}
    can never render as an actual font glyph here — that only works in
    the chat text, where a real math renderer (KaTeX/MathJax) draws it.
    The tempting shortcuts for the widget are (a) a single Unicode
    "hat" character placed next to the letters, which depends on the
    viewer's font actually containing that glyph and rendering it in a
    sane position, or (b) an ASCII character like '>' rotated -90deg
    with a CSS/SVG transform so it *looks* like a caret. Both are
    font/transform gambles. Worse, (b) can't even be expressed
    correctly here: this file's `text` op only carries {text, at,
    color} through to animateText() in prompt_builder.py, which sets
    x/y/color/font-size/font-family — there is no rotation parameter
    anywhere in that pipeline. Any code path that tried the rotated-'>'
    trick would have the transform silently dropped, leaving a plain
    unrotated '>' sitting next to the label — exactly the symptom this
    replaces. Drawing the cap as a real `arc` sidesteps glyph/transform
    fallback entirely: it's a vector path, not a character, so there is
    no font in which it can come out as the wrong shape.
    """
    # BUGFIX (cap rendering "décalé"/offset from the letters): this used to
    # place the text with a hand-guessed left-edge offset
    # ("at": [cx - 3.6 * len(text), top_y]) to fake-center left-aligned
    # (default text-anchor="start") text under a cap of a FIXED radius (8),
    # regardless of how many characters the label had. Both numbers were
    # wrong for the engine's actual rendering: animateText() in
    # prompt_builder.py draws labels at 14px sans-serif, where uppercase
    # letters average ~9-10px of advance width each — nearly double the
    # ~7.2px/char (3.6 * 2) this formula implicitly assumed. The real text
    # always rendered wider, and further right, than the guess accounted
    # for, so the cap (centered on cx, always the same 16px wide) drifted
    # out of alignment with the letters — worse for longer names, but
    # visible even for a plain 3-letter one like "AOB".
    #
    # Fix: use real centering (`text-anchor: middle`, now supported by
    # animateText() via the `anchor` op field — see prompt_builder.py)
    # instead of guessing a left-edge offset, and scale the cap radius
    # with the label length (~4.6px is roughly half the real ~9-10px/char
    # advance width of an uppercase letter at 14px sans-serif — i.e. a
    # half-width estimate, matching what `cap_r` as a radius needs) so the
    # cap's width tracks the text's real rendered width instead of staying
    # fixed at 8px for every name. The floor of 8 keeps very short labels
    # (rare — angle names are usually exactly 3 letters) from producing a
    # vanishingly small cap.
    cap_r = max(8, 4.6 * len(text))
    cap_cy = top_y - 14
    return [
        {"type": "arc", "cx": cx, "cy": cap_cy + cap_r, "r": cap_r,
         "startAngle": math.radians(-125), "endAngle": math.radians(-55),
         "color": color, "lineWidth": 1.6},
        {"type": "text", "text": text, "at": [cx, top_y], "color": color, "anchor": "middle"},
    ]


def layout_angle_pair(kind: str, **p) -> list[dict]:
    """
    Remplace le widget tabs statique de l'ancien INTERACTIVE_SVG_TEMPLATE
    (6 cas codés en dur) par une géométrie calculée avec de vraies
    fonctions trigonométriques — se généralise à n'importe quelle mesure
    d'angle, pas seulement les 6 exemples fixes de l'ancien système.
    """
    # BUGFIX: vertex_x defaulted to 150. With r_ray=140, a ray pointing
    # left (e.g. the "vertical" kind's 180° ray) reaches x = 150-140 = 10,
    # and the label-offset logic below subtracts a further 20px for any
    # leftward-pointing ray — landing the label text at x = -10, off the
    # left edge of the W=480 canvas. This wasn't a rare/extreme angle: it
    # is the DEFAULT example for "vertical" (see TestAnglePair.test_
    # vertical), i.e. every vertical-angle-pair turn was silently
    # producing an off-canvas label. 170 keeps the full ray+label reach
    # (170 - 140 - 20 = 10) safely inside the canvas on the left, while
    # still comfortably inside it on the right for every kind this
    # function supports (max reach ox + r_ray + 8 = 318, well under
    # W - margin).
    ox, oy = p.get("vertex_x", 170), p.get("vertex_y", 180)
    o_label = p.get("vertex_label", "O")
    r_ray = 140

    def ray(angle_deg, label, color):
        rad = math.radians(angle_deg)
        x = ox + r_ray * math.cos(rad)
        y = oy - r_ray * math.sin(rad)
        tx = x + (8 if math.cos(rad) >= 0 else -20)
        ty = y - (8 if math.sin(rad) >= 0 else -14)
        # BUGFIX: ray()'s computed label position was never bounds-checked
        # at all, unlike every other layout function in this file (see
        # module docstring's "no function may ever hallucinate a
        # coordinate" rule) — a non-default vertex_x/vertex_y passed in
        # (or any future kind added to this function) could silently push
        # a label off-canvas with nothing to catch it. Margin=5, matching
        # this file's other near-edge-but-legitimate cases (see
        # layout_compass_construction's bisector-endpoint check) — labels
        # can legitimately sit close to the edge, this only catches
        # genuinely off-canvas ones.
        _check_bounds(tx, ty, margin=5, context=f"angle_pair label {label!r}")
        return (
            {"type": "line", "from": [ox, oy], "to": [x, y], "color": color, "lineWidth": 2.5},
            {"type": "text", "text": label, "at": [tx, ty], "color": color},
        )

    steps = [{"label": o_label, "ops": [
        {"type": "dot", "at": [ox, oy], "color": "hi", "radius": 4},
        {"type": "text", "text": o_label, "at": [ox - 14, oy + 16], "color": "hi"},
    ]}]

    if kind == "generic":
        # No measure_degrees involved on purpose: this kind exists for
        # definitional/vocabulary points ("what is an angle", "vertex",
        # "sides") where showing a specific degree value would be
        # misleading or simply irrelevant. `angle_degrees` is accepted as
        # an internal geometry-only knob (how open the two rays look) —
        # it is NEVER rendered as text, unlike measure_degrees in the
        # acute/right/obtuse branch below. Default (55°) was picked to
        # look like an unremarkable, generic angle — not 90°/180°, which
        # would visually suggest "right angle" or "straight line" instead
        # of a plain, unlabeled angle.
        m = p.get("angle_degrees", 55)
        a_label = p.get("ray_a_label", "A")
        b_label = p.get("ray_b_label", "B")
        angle_name = p.get("angle_name", f"{a_label}{o_label}{b_label}")
        l1, t1 = ray(0, a_label, "primary")
        l2, t2 = ray(m, b_label, "secondary")
        steps.append({"label": a_label, "ops": [l1, t1]})
        steps.append({"label": b_label, "ops": [l2, t2]})
        bis = m / 2
        lx = ox + 55 * math.cos(math.radians(bis))
        ly = oy - 55 * math.sin(math.radians(bis))
        _check_bounds(lx, ly, margin=5, context=f"angle_pair widehat {angle_name!r}")
        steps.append({"label": angle_name, "ops": [
            {"type": "arc", "cx": ox, "cy": oy, "r": 40,
             "startAngle": -math.radians(m), "endAngle": 0, "color": "accent"},
            *_widehat_ops(lx, ly, angle_name, "accent"),
        ]})
        return steps

    if kind in ("acute", "right", "obtuse"):
        default = {"acute": 45, "right": 90, "obtuse": 120}[kind]
        m = p.get("measure_degrees", default)
        a_label, b_label = p.get("ray_a_label", "A"), p.get("ray_b_label", "B")
        l1, t1 = ray(0, a_label, "primary")
        l2, t2 = ray(m, b_label, "secondary")
        steps.append({"label": a_label, "ops": [l1, t1]})
        steps.append({"label": b_label, "ops": [l2, t2]})
        steps.append({"label": f"{m}°", "ops": [
            {"type": "arc", "cx": ox, "cy": oy, "r": 40,
             "startAngle": -math.radians(m), "endAngle": 0, "color": "accent"},
            {"type": "text", "text": f"{m}°", "at": [ox + 24, oy - 12], "color": "accent"},
        ]})
        return steps

    if kind in ("complementary", "supplementary"):
        total = 90 if kind == "complementary" else 180
        m1 = p.get("measure_a_degrees", total / 2)
        m2 = p.get("measure_b_degrees", total - m1)
        a_label = p.get("ray_a_label", "A")
        b_label = p.get("ray_b_label", "B")
        c_label = p.get("ray_c_label", "C")
        l1, t1 = ray(0, a_label, "primary")
        l2, t2 = ray(m1, c_label, "aux")
        l3, t3 = ray(m1 + m2, b_label, "secondary")
        steps.append({"label": a_label, "ops": [l1, t1]})
        steps.append({"label": c_label, "ops": [l2, t2]})
        steps.append({"label": b_label, "ops": [l3, t3]})
        steps.append({"label": f"{m1}° + {m2}° = {total}°", "ops": [
            {"type": "arc", "cx": ox, "cy": oy, "r": 35, "startAngle": -math.radians(m1), "endAngle": 0, "color": "accent"},
            {"type": "arc", "cx": ox, "cy": oy, "r": 35, "startAngle": -math.radians(m1 + m2), "endAngle": -math.radians(m1), "color": "hi"},
            {"type": "text", "text": f"{m1}° + {m2}° = {total}°", "at": [ox - 60, oy + 60], "color": "text"},
        ]})
        return steps

    if kind == "vertical":
        m = p.get("measure_degrees", 40)
        a_label = p.get("ray_a_label", "A")
        b_label = p.get("ray_b_label", "B")
        ap_label = p.get("ray_c_label") or p.get("ray_a_prime_label", "A'")
        bp_label = p.get("ray_d_label") or p.get("ray_b_prime_label", "B'")
        l1, t1 = ray(m, a_label, "primary")
        l2, t2 = ray(180 + m, ap_label, "primary")
        l3, t3 = ray(0, b_label, "secondary")
        l4, t4 = ray(180, bp_label, "secondary")
        steps.append({"label": f"{a_label}{ap_label}", "ops": [l1, t1, l2, t2]})
        steps.append({"label": f"{b_label}{bp_label}", "ops": [l3, t3, l4, t4]})
        steps.append({"label": "α = α'", "ops": [
            {"type": "arc", "cx": ox, "cy": oy, "r": 30, "startAngle": 0, "endAngle": -math.radians(m), "color": "accent"},
            {"type": "arc", "cx": ox, "cy": oy, "r": 30, "startAngle": math.pi, "endAngle": math.pi - math.radians(m), "color": "accent"},
            {"type": "text", "text": "α = α'", "at": [ox - 20, oy + 55], "color": "accent"},
        ]})
        return steps

    if kind == "adjacent":
        # Trois rayons OA/OB/OC partageant le rayon OB: deux angles
        # adjacents ∠AOB et ∠BOC, non-superposés, dont la somme est ∠AOC.
        # Placés en angles positifs (0 → m1 → m1+m2), comme complementary/
        # supplementary ci-dessus, pour rester dans le demi-plan haut du
        # canvas (voir le commentaire de ray() sur vertex_y=180 et la
        # marge verticale disponible).
        m1 = p.get("angle_aob_degrees", 35)
        m2 = p.get("angle_boc_degrees", 45)
        a_label = p.get("ray_a_label", "A")
        b_label = p.get("ray_b_label", "B")
        c_label = p.get("ray_c_label", "C")
        l1, t1 = ray(0, a_label, "primary")
        l2, t2 = ray(m1, b_label, "secondary")
        l3, t3 = ray(m1 + m2, c_label, "aux")
        steps.append({"label": a_label, "ops": [l1, t1]})
        steps.append({"label": b_label, "ops": [l2, t2]})
        steps.append({"label": c_label, "ops": [l3, t3]})

        name_ab = f"{a_label}{o_label}{b_label}"
        name_bc = f"{b_label}{o_label}{c_label}"
        bis_ab, bis_bc = m1 / 2, m1 + m2 / 2
        x_ab = ox + 55 * math.cos(math.radians(bis_ab))
        y_ab = oy - 55 * math.sin(math.radians(bis_ab))
        x_bc = ox + 60 * math.cos(math.radians(bis_bc))
        y_bc = oy - 60 * math.sin(math.radians(bis_bc))
        _check_bounds(x_ab, y_ab, margin=5, context=f"angle_pair widehat {name_ab!r}")
        _check_bounds(x_bc, y_bc, margin=5, context=f"angle_pair widehat {name_bc!r}")
        steps.append({"label": name_ab, "ops": [
            {"type": "arc", "cx": ox, "cy": oy, "r": 30,
             "startAngle": -math.radians(m1), "endAngle": 0, "color": "accent"},
            *_widehat_ops(x_ab, y_ab, name_ab, "accent"),
        ]})
        steps.append({"label": name_bc, "ops": [
            {"type": "arc", "cx": ox, "cy": oy, "r": 30,
             "startAngle": -math.radians(m1 + m2), "endAngle": -math.radians(m1), "color": "hi"},
            *_widehat_ops(x_bc, y_bc, name_bc, "hi"),
        ]})
        return steps

    raise ValueError(f"unknown angle_pair kind: {kind!r}")


# ═══════════════════════ LAYOUT: triangle_angle_sum ═══════════════════════

_TRIANGLE_VARIANTS = {
    # Fixed, hand-checked-in-bounds vertex sets (W=480, H=300). Real
    # coordinates, not hallucinated — interior angles below are computed
    # FROM these points via atan2 (see _interior_angle_arc), never a
    # guessed/fixed degree value.
    "scalene":   [(90, 250), (410, 250), (260, 60)],
    "right":     [(90, 250), (90, 60),  (410, 250)],
    "isosceles": [(90, 250), (410, 250), (250, 55)],
}


def _interior_angle_arc(vertices: list[tuple[float, float]], i: int) -> tuple[float, float]:
    """
    Returns (startAngle, endAngle) for the interior angle AT vertex i of a
    polygon, computed from the two adjacent vertices' real positions via
    atan2 — never a guessed/fixed degree value. Works for any N-gon, so
    it's shared between triangle_angle_sum and quadrilateral_angle_sum
    below.
    """
    n = len(vertices)
    cx, cy = vertices[i]
    prev_x, prev_y = vertices[(i - 1) % n]
    next_x, next_y = vertices[(i + 1) % n]
    a1 = math.atan2(-(prev_y - cy), prev_x - cx)
    a2 = math.atan2(-(next_y - cy), next_x - cx)

    # BUGFIX: the JS renderer (prompt_builder.py animateArc/_arcPath) draws
    # arc ops with x=cx+r*cos(a), y=cy+r*sin(a) — NO y-flip. But a1/a2
    # above are solved via atan2(-(dy), dx), which is the FLIPPED
    # convention (y=cy-r*sin(a)) used by every point-placement helper in
    # this file. Every other arc-emitting function that goes through this
    # flipped convention (compass_construction, angle_pair) negates the
    # angle before handing it to an arc op; this function used to skip
    # that step, so every interior-angle arc rendered as the vertical
    # mirror image of the real angle — flung away from the shape's
    # interior instead of curving into the vertex. See conversation/bug
    # report for the numeric proof (isosceles triangle apex C).
    a1, a2 = -a1, -a2

    # BUGFIX: also normalize a2 into (a1-pi, a1+pi] so the arc always
    # sweeps the actual (short) interior angle, not the reflex way around.
    # Without this, a vertex whose two edge directions straddle atan2's
    # +-180 degree discontinuity (e.g. a rectangle's top-right corner,
    # where one edge points along +180 deg and the other along -90 deg)
    # got a raw a2-a1 of ~270 degrees instead of the true 90 degree
    # interior angle — confirmed on quadrilateral_angle_sum's "rectangle",
    # "parallelogram", and "generic" variants, all at vertex C.
    diff = (a2 - a1 + math.pi) % (2 * math.pi) - math.pi
    a2 = a1 + diff
    return a1, a2


def layout_triangle_angle_sum(variant: str = "scalene",
                               a_label: str = "A", b_label: str = "B",
                               c_label: str = "C",
                               show_sum_formula: bool = True) -> list[dict]:
    if variant not in _TRIANGLE_VARIANTS:
        raise ValueError(f"unknown triangle_angle_sum variant: {variant!r}")
    verts = _TRIANGLE_VARIANTS[variant]
    labels = [a_label, b_label, c_label]
    colors = ["primary", "secondary", "accent"]

    for (vx, vy) in verts:
        _check_bounds(vx, vy, margin=20, context="triangle_angle_sum vertex")

    side_ops = []
    for i in range(3):
        x1, y1 = verts[i]
        x2, y2 = verts[(i + 1) % 3]
        side_ops.append({"type": "line", "from": [x1, y1], "to": [x2, y2],
                          "color": "text", "lineWidth": 2.5})
    steps = [{"label": "المثلث", "ops": side_ops}]

    # One step per vertex: dot + label + interior-angle arc, using REAL
    # angles computed from the fixed coordinates above (see
    # _interior_angle_arc) — never a hand-picked degree number.
    for i in range(3):
        vx, vy = verts[i]
        label_dx = 14 if vx < 240 else (-24 if vx > 240 else -5)
        label_dy = -12 if vy < 150 else 22
        lx, ly = vx + label_dx, vy + label_dy
        _check_bounds(lx, ly, margin=5, context=f"triangle_angle_sum label {labels[i]!r}")
        a1, a2 = _interior_angle_arc(verts, i)
        steps.append({"label": labels[i], "ops": [
            {"type": "dot", "at": [vx, vy], "color": "hi", "radius": 4},
            {"type": "text", "text": labels[i], "at": [lx, ly], "color": colors[i]},
            {"type": "arc", "cx": vx, "cy": vy, "r": 22,
             "startAngle": a1, "endAngle": a2, "color": colors[i]},
        ]})

    if show_sum_formula:
        steps.append({"label": f"{a_label}+{b_label}+{c_label}=180°", "ops": [
            {"type": "text", "text": f"{a_label} + {b_label} + {c_label} = 180°",
             "at": [150, 285], "color": "text"},
        ]})
    return steps


# ═══════════════════════ LAYOUT: quadrilateral_angle_sum ═══════════════════════

_QUAD_VARIANTS = {
    "generic":       [(90, 230), (370, 250), (410, 90), (140, 60)],
    "rectangle":     [(90, 230), (390, 230), (390, 60), (90, 60)],
    "parallelogram": [(90, 230), (350, 230), (410, 60), (150, 60)],
}


def layout_quadrilateral_angle_sum(variant: str = "generic",
                                    a_label: str = "A", b_label: str = "B",
                                    c_label: str = "C", d_label: str = "D",
                                    show_sum_formula: bool = True) -> list[dict]:
    if variant not in _QUAD_VARIANTS:
        raise ValueError(f"unknown quadrilateral_angle_sum variant: {variant!r}")
    verts = _QUAD_VARIANTS[variant]
    labels = [a_label, b_label, c_label, d_label]
    colors = ["primary", "secondary", "accent", "aux"]

    for (vx, vy) in verts:
        _check_bounds(vx, vy, margin=20, context="quadrilateral_angle_sum vertex")

    side_ops = []
    for i in range(4):
        x1, y1 = verts[i]
        x2, y2 = verts[(i + 1) % 4]
        side_ops.append({"type": "line", "from": [x1, y1], "to": [x2, y2],
                          "color": "text", "lineWidth": 2.5})
    steps = [{"label": "الرباعي", "ops": side_ops}]

    for i in range(4):
        vx, vy = verts[i]
        label_dx = 14 if vx < 240 else -24
        label_dy = 20 if vy > 150 else -12
        lx, ly = vx + label_dx, vy + label_dy
        _check_bounds(lx, ly, margin=5, context=f"quadrilateral_angle_sum label {labels[i]!r}")
        a1, a2 = _interior_angle_arc(verts, i)
        steps.append({"label": labels[i], "ops": [
            {"type": "dot", "at": [vx, vy], "color": "hi", "radius": 4},
            {"type": "text", "text": labels[i], "at": [lx, ly], "color": colors[i]},
            {"type": "arc", "cx": vx, "cy": vy, "r": 20,
             "startAngle": a1, "endAngle": a2, "color": colors[i]},
        ]})

    if show_sum_formula:
        steps.append({"label": f"{a_label}+{b_label}+{c_label}+{d_label}=360°", "ops": [
            {"type": "text", "text": f"{a_label} + {b_label} + {c_label} + {d_label} = 360°",
             "at": [110, 285], "color": "text"},
        ]})
    return steps


# ═══════════════════ LAYOUT: circle_line_position ═══════════════════
# Added to fill a real gap: the registry previously had no representation
# for a circle at all (mirror_symmetry's "circle" variant is about axial
# symmetry, not this) — so a lesson point on "sécante/tangente/extérieure"
# had no valid menu option and fell to "none", losing a diagram for a
# topic that's genuinely visual (distance-to-center vs. radius). Same
# "derive, don't hallucinate" discipline as the rest of this file: the
# circle's center/radius and the line's position are fixed constants (this
# representation is conceptual, not tied to a numeric worked example), and
# every intersection/tangency/foot point is computed via real geometry
# (Pythagorean offset for the secant case) rather than guessed pixels.

def layout_circle_line_position(variant: str = "secant", **p) -> list[dict]:
    if variant not in ("secant", "tangent", "exterior"):
        raise ValueError(f"unknown circle_line_position variant: {variant!r}")

    ox, oy = 200, 150
    r = 80
    o_label    = p.get("center_label", "O")
    r_label    = p.get("radius_label", "r")
    line_label = p.get("line_label", "(d)")
    h_label    = p.get("foot_label", "H")

    line_x1, line_x2 = 40, W - 40

    # Distance from O to the line — fixed per variant so the drawing
    # always matches the case it's meant to illustrate:
    #   secant:   d < r  (line crosses the circle twice)
    #   tangent:  d == r (line touches the circle at exactly one point)
    #   exterior: d > r  (line misses the circle entirely)
    d = {"secant": 45, "tangent": r, "exterior": 130}[variant]
    line_y = oy + d
    _check_bounds(ox, line_y, margin=10, context="circle_line_position line foot")

    steps = [{"label": o_label, "ops": [
        {"type": "arc", "cx": ox, "cy": oy, "r": r, "startAngle": 0, "endAngle": 6.283, "color": "primary"},
        {"type": "dot", "at": [ox, oy], "color": "hi", "radius": 4},
        {"type": "text", "text": o_label, "at": [ox - 8, oy - 10], "color": "hi"},
    ]}]

    steps.append({"label": line_label, "ops": [
        {"type": "line", "from": [line_x1, line_y], "to": [line_x2, line_y],
         "color": "secondary", "lineWidth": 2},
        {"type": "text", "text": line_label, "at": [line_x1 - 10, line_y + 20], "color": "secondary"},
    ]})

    if variant == "secant":
        # Real circle-line intersection: offset from the foot of the
        # perpendicular = sqrt(r^2 - d^2), derived from the right triangle
        # (O, foot, intersection point) — same discipline as
        # compass_construction's crossing-point math above.
        offset = math.sqrt(max(r * r - d * d, 0.0))
        if offset < 10:
            raise ValueError(
                "circle_line_position: secant offset too small for a "
                "clean drawing (d too close to r)"
            )
        e_label = p.get("point_a_label", "E")
        f_label = p.get("point_b_label", "F")
        ex_, fx_ = ox - offset, ox + offset
        _check_bounds(ex_, line_y, margin=5, context="circle_line_position point E")
        _check_bounds(fx_, line_y, margin=5, context="circle_line_position point F")
        steps.append({"label": f"{e_label}, {f_label}", "ops": [
            {"type": "dot", "at": [ex_, line_y], "color": "hi", "radius": 4},
            {"type": "text", "text": e_label, "at": [ex_ - 8, line_y - 12], "color": "hi"},
            {"type": "dot", "at": [fx_, line_y], "color": "hi", "radius": 4},
            {"type": "text", "text": f_label, "at": [fx_ + 6, line_y - 12], "color": "hi"},
        ]})
        steps.append({"label": h_label, "ops": [
            {"type": "line", "from": [ox, oy], "to": [ox, line_y], "color": "aux",
             "lineWidth": 1.5, "dash": [5, 4]},
            {"type": "dot", "at": [ox, line_y], "color": "hi", "radius": 3},
            {"type": "text", "text": h_label, "at": [ox + 6, line_y - 6], "color": "hi"},
            {"type": "line", "from": [ox - 10, line_y], "to": [ox - 10, line_y - 10],
             "color": "aux", "lineWidth": 1.5},
            {"type": "line", "from": [ox - 10, line_y - 10], "to": [ox, line_y - 10],
             "color": "aux", "lineWidth": 1.5},
        ]})

    elif variant == "tangent":
        # d == r exactly, so the foot of the perpendicular IS the point of
        # tangency — the radius OT drawn solid (not dashed) is the
        # defining property students need to see: OT ⟂ (d) at T.
        t_label = p.get("point_label", h_label)
        steps.append({"label": t_label, "ops": [
            {"type": "line", "from": [ox, oy], "to": [ox, line_y], "color": "accent", "lineWidth": 2.5},
            {"type": "dot", "at": [ox, line_y], "color": "hi", "radius": 4},
            {"type": "text", "text": t_label, "at": [ox + 6, line_y - 6], "color": "hi"},
            {"type": "text", "text": r_label, "at": [ox + 8, oy + d / 2], "color": "accent"},
            {"type": "line", "from": [ox - 10, line_y], "to": [ox - 10, line_y - 10],
             "color": "accent", "lineWidth": 1.5},
            {"type": "line", "from": [ox - 10, line_y - 10], "to": [ox, line_y - 10],
             "color": "accent", "lineWidth": 1.5},
        ]})

    else:  # exterior
        # d > r: the perpendicular from O reaches the line, but a separate
        # marker on the circle itself (at distance r along the same
        # direction) makes the "distance > radius" comparison visible
        # rather than just implied by the gap between the line and circle.
        steps.append({"label": h_label, "ops": [
            {"type": "line", "from": [ox, oy], "to": [ox, line_y], "color": "aux",
             "lineWidth": 1.5, "dash": [5, 4]},
            {"type": "dot", "at": [ox, line_y], "color": "hi", "radius": 3},
            {"type": "text", "text": h_label, "at": [ox + 6, line_y - 6], "color": "hi"},
            {"type": "line", "from": [ox - 10, line_y], "to": [ox - 10, line_y - 10],
             "color": "aux", "lineWidth": 1.5},
            {"type": "line", "from": [ox - 10, line_y - 10], "to": [ox, line_y - 10],
             "color": "aux", "lineWidth": 1.5},
        ]})
        steps.append({"label": r_label, "ops": [
            {"type": "dot", "at": [ox, oy + r], "color": "hi", "radius": 3},
            {"type": "text", "text": r_label, "at": [ox + 8, oy + r - 6], "color": "primary"},
        ]})

    return steps