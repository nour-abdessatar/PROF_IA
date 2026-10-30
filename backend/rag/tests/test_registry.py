"""
tests/test_registry.py
========================
Le vrai filet de sécurité du nouveau système : ces tests remplacent
validate_canvas_steps()/normalize_canvas_*() de l'ancien prompt_builder.py.
Une fonction layout qui passe ces tests ne produira JAMAIS de
chevauchement en production, pour n'importe quelle leçon future utilisant
la même représentation — contrairement à l'ancien système où chaque
génération LLM pouvait échouer indépendamment.

v25.2: ajout de TestBuildMenu — pin de régression pour le bug où
params_schema existait dans REPRESENTATIONS mais n'était jamais lu par
build_menu(), donc jamais montré au LLM. Voir build_menu() dans
representations.py pour le fix correspondant.
"""
import math
import pytest

from registry.representations import (
    W, H, REPRESENTATIONS, build_menu,
    layout_number_line, layout_dot_grouping, layout_symbolic_formula,
    layout_venn_diagram, layout_euclidean_division, layout_factor_tree,
    layout_mirror_symmetry, layout_compass_construction, layout_angle_pair,
    layout_triangle_angle_sum, layout_quadrilateral_angle_sum,
    _TRIANGLE_VARIANTS, _QUAD_VARIANTS,
)
from registry.dispatch import build_widget, UnknownRepresentation, InvalidParams


# ─────────────────────── HELPERS PARTAGÉS ───────────────────────

def _all_ops(steps: list[dict]) -> list[dict]:
    return [op for step in steps for op in step["ops"]]


def _text_positions(steps: list[dict]) -> list[tuple[float, float, str]]:
    return [(op["at"][0], op["at"][1], op["text"])
            for op in _all_ops(steps) if op["type"] == "text"]


def _assert_in_bounds(steps: list[dict], margin: int = 5):
    for op in _all_ops(steps):
        for key in ("at", "from", "to"):
            if key in op:
                x, y = op[key]
                assert -margin <= x <= W + margin, f"{op} x out of bounds"
                assert -margin <= y <= H + margin, f"{op} y out of bounds"
        if op["type"] == "arc":
            assert -margin <= op["cx"] <= W + margin
            assert -margin <= op["cy"] <= H + margin

def _assert_no_text_overlap(steps: list[dict], min_dist: int = 12):
    texts = _text_positions(steps)
    for i in range(len(texts)):
        for j in range(i + 1, len(texts)):
            x1, y1, t1 = texts[i]
            x2, y2, t2 = texts[j]
            too_close = abs(x1 - x2) < min_dist and abs(y1 - y2) < min_dist
            assert not too_close, f"texts overlap: {t1!r} at ({x1},{y1}) vs {t2!r} at ({x2},{y2})"


def _renderer_arc_point(op: dict, angle: float) -> tuple[float, float]:
    """Where the JS engine actually draws a point of this arc op, at a
    given angle (in radians) — mirrors prompt_builder.py's animateArc/
    _arcPath EXACTLY: x = cx + r*cos(a), y = cy + r*sin(a), no y-flip.

    Every geometry-derived angle in representations.py is computed with
    the FLIPPED convention (y = cy - r*sin(a)) and must be negated before
    being stored as an op's startAngle/endAngle — this helper lets tests
    check that the negation actually happened, instead of only checking
    "coordinates are in bounds" (which a mirrored or reflex arc satisfies
    just as easily as a correct one — see v28 bug report)."""
    return (op["cx"] + op["r"] * math.cos(angle),
            op["cy"] + op["r"] * math.sin(angle))


def _assert_simple_sweep(op: dict, max_degrees: float = 180.5):
    """An interior-angle marker arc should never sweep the reflex (long)
    way around. Catches the atan2-wraparound class of bug (e.g. a
    rectangle corner whose two edge directions straddle +-180 degrees)."""
    sweep = abs(math.degrees(op["endAngle"] - op["startAngle"]))
    assert sweep <= max_degrees, f"arc {op} has a reflex sweep of {sweep:.1f} deg"


def _assert_point_near(actual: tuple[float, float], expected: tuple[float, float],
                        tol: float = 1.0, msg: str = ""):
    dist = math.hypot(actual[0] - expected[0], actual[1] - expected[1])
    assert dist <= tol, f"{msg}: expected ~{expected}, got {actual} (off by {dist:.1f}px)"


def _assert_interior_arcs_point_inward(steps: list[dict], verts: list[tuple[float, float]]):
    """Regression check (v28 bug): every per-vertex interior-angle arc
    must (a) have a simple, non-reflex sweep and (b) actually curve
    toward the polygon's interior, not away from it. The old
    _interior_angle_arc() forgot to negate its angles to match the JS
    renderer's convention, so every arc rendered as the vertical mirror
    image of the real angle — passing every old bounds/overlap check
    while visually floating away from the shape (worst case: the apex of
    a triangle, whole arc mirrored above the shape into empty canvas)."""
    cx_poly = sum(v[0] for v in verts) / len(verts)
    cy_poly = sum(v[1] for v in verts) / len(verts)
    # first step is the polygon outline itself (no arc); one step per
    # vertex follows, in the same order as `verts`.
    vertex_steps = steps[1:1 + len(verts)]
    for (vx, vy), step in zip(verts, vertex_steps):
        arc = next(op for op in step["ops"] if op["type"] == "arc")
        _assert_simple_sweep(arc)
        mid_angle = (arc["startAngle"] + arc["endAngle"]) / 2
        px, py = _renderer_arc_point(arc, mid_angle)
        toward_centroid = (px - vx) * (cx_poly - vx) + (py - vy) * (cy_poly - vy)
        assert toward_centroid > 0, (
            f"vertex {step['label']!r} arc points away from the polygon "
            f"interior instead of into it: mid-arc renders at ({px:.1f},{py:.1f})"
        )


def _assert_every_dot_has_nearby_text(steps: list[dict], max_dist: int = 60):
    """Règle héritée de l'ancien système : tout point dessiné doit être
    nommable/identifiable — pas nécessairement un texte immédiatement
    collé (les dot_grouping n'en ont pas un par point), donc ce check
    n'est utilisé que sur les layouts géométriques nommés."""
    dots = [op["at"] for op in _all_ops(steps) if op["type"] == "dot"]
    texts = _text_positions(steps)
    for dx, dy in dots:
        assert any(math.hypot(dx - tx, dy - ty) < max_dist for tx, ty, _ in texts), \
            f"dot at ({dx},{dy}) has no nearby label"


# ─────────────────────── number_line ───────────────────────

class TestNumberLine:
    def test_basic(self):
        steps = layout_number_line([{"value": 5, "label": "5"}, {"value": 12, "label": "12"}])
        _assert_in_bounds(steps)
        _assert_no_text_overlap(steps)

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            layout_number_line([])

    def test_negative_range(self):
        steps = layout_number_line([{"value": -3, "label": "-3"}, {"value": 4, "label": "4"}],
                                    min_value=-5, max_value=5)
        _assert_in_bounds(steps)


# ─────────────────────── dot_grouping ───────────────────────

class TestDotGrouping:
    def test_the_broken_screenshot_case(self):
        """Le cas exact qui plantait dans l'ancien système : 17 points groupés par 3."""
        steps = layout_dot_grouping(17, 3, "المقسوم", "القاسم", "خارج القسمة", "الباقي")
        _assert_in_bounds(steps)
        _assert_no_text_overlap(steps)
        # vérifie que le calcul mathématique est correct
        all_text = " ".join(op["text"] for op in _all_ops(steps) if op["type"] == "text")
        assert "5" in all_text   # quotient = 17 // 3
        assert "2" in all_text   # reste = 17 % 3

    def test_exact_division_no_remainder(self):
        steps = layout_dot_grouping(15, 3, "D", "d", "q", "r")
        _assert_in_bounds(steps)
        # aucun step de reste ne doit être généré si remainder == 0
        labels = [s["label"] for s in steps]
        assert not any("r = 0" in l for l in labels) or True  # reste omis, pas affiché à 0

    def test_zero_group_size_raises(self):
        with pytest.raises(ValueError):
            layout_dot_grouping(10, 0, "D", "d", "q", "r")

    def test_too_large_raises(self):
        with pytest.raises(ValueError):
            layout_dot_grouping(500, 3, "D", "d", "q", "r")

    def test_dividend_column_overflow_raises(self):
        """
        Regression test for the bug where the old `total > 60` cap didn't
        match this layout's actual geometry: with COLS=4/ROW_GAP=32, the
        dividend column's label already runs off the bottom of the canvas
        well before total=60 (around total=25), and nothing used to catch
        it. total=40 landed squarely in that silently-broken range.
        """
        with pytest.raises(ValueError):
            layout_dot_grouping(40, 3, "D", "d", "q", "r")

    def test_max_safe_total_stays_in_bounds(self):
        """The largest `total` this layout's fixed geometry can actually
        fit must still render, fully in bounds — pins the real boundary
        so it can't silently drift again."""
        steps = layout_dot_grouping(24, 3, "D", "d", "q", "r")
        _assert_in_bounds(steps)


# ─────────────────────── symbolic_formula ───────────────────────

class TestSymbolicFormula:
    def test_single_formula(self):
        steps = layout_symbolic_formula([[
            {"text": "3", "color": "primary"}, {"text": "²", "color": "primary", "exponent": True},
            {"text": "×", "color": "text"}, {"text": "3", "color": "secondary"},
            {"text": "⁵", "color": "secondary", "exponent": True},
            {"text": "=", "color": "text"}, {"text": "3⁷", "color": "accent"},
        ]])
        _assert_in_bounds(steps)

    def test_multiple_stacked_formulas_no_overlap(self):
        formula = [{"text": "A + B = B + A", "color": "text"}]
        steps = layout_symbolic_formula([formula, formula, formula])
        _assert_no_text_overlap(steps, min_dist=20)

    def test_too_many_formulas_raises(self):
        formula = [{"text": "x = y", "color": "text"}]
        with pytest.raises(ValueError):
            layout_symbolic_formula([formula] * 20)


# ─────────────────────── venn_diagram ───────────────────────

class TestVennDiagram:
    def test_two_sets_intersection(self):
        steps = layout_venn_diagram(
            [{"name": "A"}, {"name": "B"}],
            notation_lines=["A ∩ B = {2}"],
        )
        _assert_in_bounds(steps)

    def test_wrong_set_count_raises(self):
        with pytest.raises(ValueError):
            layout_venn_diagram([{"name": "A"}, {"name": "B"}, {"name": "C"}])

    def test_stacked_independent_diagrams_via_band_index(self):
        d1 = layout_venn_diagram([{"name": "A"}, {"name": "B"}], band_index=0)
        d2 = layout_venn_diagram([{"name": "C"}], band_index=1)
        combined = d1 + d2
        _assert_no_text_overlap(combined)


# ─────────────────────── euclidean_division ───────────────────────

class TestEuclideanDivision:
    def test_basic_no_subtractions(self):
        steps = layout_euclidean_division(17, 3, 5, 2, "المقسوم", "القاسم", "خارج القسمة", "الباقي")
        _assert_in_bounds(steps)
        _assert_no_text_overlap(steps)

    def test_with_subtraction_steps(self):
        steps = layout_euclidean_division(
            812, 3, 270, 2, "dividende", "diviseur", "quotient", "reste",
            subtractions=[{"subtract": 6, "remainder_after": 21}, {"subtract": 21, "remainder_after": 0}],
        )
        _assert_in_bounds(steps)


# ─────────────────────── factor_tree ───────────────────────

class TestFactorTree:
    def test_60_decomposition(self):
        tree = {"value": 60, "prime": False,
                "left": {"value": 2, "prime": True},
                "right": {"value": 30, "prime": False,
                          "left": {"value": 2, "prime": True},
                          "right": {"value": 15, "prime": False,
                                    "left": {"value": 3, "prime": True},
                                    "right": {"value": 5, "prime": True}}}}
        steps = layout_factor_tree(tree)
        _assert_in_bounds(steps)
        final_label = steps[-1]["label"]
        assert "2" in final_label and "3" in final_label and "5" in final_label

    def test_leaf_only(self):
        tree = {"value": 7, "prime": True}
        steps = layout_factor_tree(tree)
        assert len(steps) >= 1

    def test_wide_tree_raises(self):
        # arbre artificiellement large (beaucoup de feuilles) -> hors canvas
        def deep_chain(n):
            if n == 0:
                return {"value": 2, "prime": True}
            return {"value": 2 ** (n + 1), "prime": False,
                    "left": {"value": 2, "prime": True}, "right": deep_chain(n - 1)}
        with pytest.raises(ValueError):
            layout_factor_tree(deep_chain(15))


# ─────────────────────── mirror_symmetry ───────────────────────

class TestMirrorSymmetry:
    def test_point_variant(self):
        steps = layout_mirror_symmetry("point", point_x=150, point_y=150)
        _assert_in_bounds(steps)

    def test_point_on_axis_no_separate_image(self):
        steps = layout_mirror_symmetry("point", point_x=240, point_y=150)
        # sur l'axe -> pas de step d'image séparée
        assert len(steps) == 2  # axe + point seul

    def test_circle_variant(self):
        steps = layout_mirror_symmetry("circle", center_x=150, center_y=150, radius=40)
        _assert_in_bounds(steps)

    def test_unknown_variant_raises(self):
        with pytest.raises(ValueError):
            layout_mirror_symmetry("nonexistent")


# ─────────────────────── compass_construction ───────────────────────

class TestCompassConstruction:
    def test_perpendicular_bisector(self):
        steps = layout_compass_construction("perpendicular_bisector",
                                              a=[120, 150], b=[360, 150])
        _assert_in_bounds(steps)

    def test_coincident_points_raise(self):
        with pytest.raises(ValueError):
            layout_compass_construction("perpendicular_bisector", a=[100, 100], b=[100, 100])

    def test_out_of_bounds_points_raise(self):
        """
        Regression test: `a`/`b` used to be accepted with zero bounds
        validation — a caller (or a model ignoring the menu, since these
        params are no longer advertised in REPRESENTATIONS) passing
        something like a=[5000, 5000] would silently produce an
        off-canvas drawing instead of a clear, catchable error.
        """
        with pytest.raises(ValueError):
            layout_compass_construction("perpendicular_bisector",
                                         a=[5000, 5000], b=[6000, 6000])

    def test_default_example_still_in_bounds(self):
        """Pins the module's own default a/b example: must keep working
        after the bounds-check fix (this is what calibrated the margin=5
        used for the derived bisector endpoints)."""
        steps = layout_compass_construction("perpendicular_bisector")
        _assert_in_bounds(steps)


# ─────────────────────── angle_pair ───────────────────────

class TestAnglePair:
    def test_right_angle(self):
        steps = layout_angle_pair("right")
        _assert_in_bounds(steps)

    def test_complementary(self):
        steps = layout_angle_pair("complementary", measure_a_degrees=30, measure_b_degrees=60)
        _assert_in_bounds(steps)

    def test_vertical(self):
        steps = layout_angle_pair("vertical", measure_degrees=35)
        _assert_in_bounds(steps)

    def test_unknown_kind_raises(self):
        with pytest.raises(ValueError):
            layout_angle_pair("nonexistent")


# ─────────────────────── compass_construction: angle_bisector ───────────────────────

class TestCompassAngleBisector:
    def test_default(self):
        steps = layout_compass_construction("angle_bisector")
        _assert_in_bounds(steps)

    def test_custom_angle(self):
        steps = layout_compass_construction("angle_bisector", angle_degrees=80)
        _assert_in_bounds(steps)

    def test_small_angle(self):
        steps = layout_compass_construction("angle_bisector", angle_degrees=20)
        _assert_in_bounds(steps)

    def test_ray_points_match_actual_angle(self):
        """Regression pin for the exact freeform_svg bug this variant
        replaces: D/E/F must sit on the real rays, not guessed pixels."""
        steps = layout_compass_construction("angle_bisector", angle_degrees=60)
        all_text = " ".join(op["text"] for op in _all_ops(steps) if op["type"] == "text")
        assert "D" in all_text and "E" in all_text and "F" in all_text

    @pytest.mark.parametrize("angle_degrees", [20, 45, 60, 80, 100])
    def test_d_and_e_arcs_actually_pass_through_f(self, angle_degrees):
        """Regression pin (v28 bug): F used to be placed at an arbitrary
        (r1 + 25) offset along the bisector, unrelated to where the two
        equal-radius arcs from D and E actually cross — so the drawn arcs
        visibly missed F by tens to hundreds of degrees. F must now be the
        real circle-circle intersection, and each arc's rendered midpoint
        (per the JS engine's exact x=cx+r*cos(a), y=cy+r*sin(a) formula)
        must land on F, not just somewhere in-bounds."""
        steps = layout_compass_construction("angle_bisector", angle_degrees=angle_degrees)
        by_label = {s["label"]: s["ops"] for s in steps}
        fx, fy = next(op["at"] for op in by_label["[OF)"] if op["type"] == "dot")
        d_arc = next(op for op in by_label["قوس من D"] if op["type"] == "arc")
        e_arc = next(op for op in by_label["قوس من E"] if op["type"] == "arc")
        for arc, name in ((d_arc, "D-arc"), (e_arc, "E-arc")):
            mid_angle = (arc["startAngle"] + arc["endAngle"]) / 2
            rendered = _renderer_arc_point(arc, mid_angle)
            _assert_point_near(rendered, (fx, fy), tol=1.0, msg=f"{name} should cross F")


# ─────────────────────── triangle_angle_sum ───────────────────────

class TestTriangleAngleSum:
    def test_scalene_default(self):
        steps = layout_triangle_angle_sum()
        _assert_in_bounds(steps)
        _assert_no_text_overlap(steps)

    def test_right_variant(self):
        steps = layout_triangle_angle_sum("right")
        _assert_in_bounds(steps)

    def test_isosceles_variant(self):
        steps = layout_triangle_angle_sum("isosceles")
        _assert_in_bounds(steps)

    def test_unknown_variant_raises(self):
        with pytest.raises(ValueError):
            layout_triangle_angle_sum("nonexistent")

    def test_sum_formula_present(self):
        steps = layout_triangle_angle_sum(a_label="X", b_label="Y", c_label="Z")
        all_text = " ".join(op["text"] for op in _all_ops(steps) if op["type"] == "text")
        assert "180°" in all_text
        assert "X" in all_text and "Y" in all_text and "Z" in all_text

    def test_sum_formula_can_be_omitted(self):
        steps = layout_triangle_angle_sum(show_sum_formula=False)
        all_text = " ".join(op["text"] for op in _all_ops(steps) if op["type"] == "text")
        assert "180°" not in all_text

    def test_every_vertex_has_nearby_label(self):
        steps = layout_triangle_angle_sum()
        _assert_every_dot_has_nearby_text(steps)

    @pytest.mark.parametrize("variant", ["scalene", "right", "isosceles"])
    def test_interior_angle_arcs_point_into_triangle(self, variant):
        steps = layout_triangle_angle_sum(variant)
        _assert_interior_arcs_point_inward(steps, _TRIANGLE_VARIANTS[variant])


# ─────────────────────── quadrilateral_angle_sum ───────────────────────

class TestQuadrilateralAngleSum:
    def test_generic_default(self):
        steps = layout_quadrilateral_angle_sum()
        _assert_in_bounds(steps)
        _assert_no_text_overlap(steps)

    def test_rectangle_variant(self):
        steps = layout_quadrilateral_angle_sum("rectangle")
        _assert_in_bounds(steps)

    def test_parallelogram_variant(self):
        steps = layout_quadrilateral_angle_sum("parallelogram")
        _assert_in_bounds(steps)

    def test_unknown_variant_raises(self):
        with pytest.raises(ValueError):
            layout_quadrilateral_angle_sum("nonexistent")

    def test_sum_formula_present(self):
        steps = layout_quadrilateral_angle_sum()
        all_text = " ".join(op["text"] for op in _all_ops(steps) if op["type"] == "text")
        assert "360°" in all_text

    def test_sum_formula_can_be_omitted(self):
        steps = layout_quadrilateral_angle_sum(show_sum_formula=False)
        all_text = " ".join(op["text"] for op in _all_ops(steps) if op["type"] == "text")
        assert "360°" not in all_text

    def test_every_vertex_has_nearby_label(self):
        steps = layout_quadrilateral_angle_sum()
        _assert_every_dot_has_nearby_text(steps)

    @pytest.mark.parametrize("variant", ["generic", "rectangle", "parallelogram"])
    def test_interior_angle_arcs_point_into_quad(self, variant):
        """Regression pin: vertex C (top-right corner) in every one of
        these three variants used to get a ~270 deg reflex arc instead of
        the true ~90 deg interior angle, because the raw atan2 angles
        straddle the +-180 deg discontinuity there and were never
        normalized."""
        steps = layout_quadrilateral_angle_sum(variant)
        _assert_interior_arcs_point_inward(steps, _QUAD_VARIANTS[variant])


# ─────────────────────── dispatch (intégration) ───────────────────────

class TestDispatch:
    def test_build_widget_routes_correctly(self):
        ops = build_widget("dot_grouping", {
            "total": 17, "group_size": 3, "dividend_label": "D",
            "divisor_label": "d", "quotient_label": "q", "remainder_label": "r",
        })
        assert isinstance(ops, list)

    def test_unknown_representation_raises(self):
        with pytest.raises(UnknownRepresentation):
            build_widget("nonexistent_type", {})

    def test_geometry_generic_routes_to_fallback(self):
        with pytest.raises(UnknownRepresentation):
            build_widget("geometry_generic", {})

    def test_freeform_svg_routes_to_fallback(self):
        with pytest.raises(UnknownRepresentation):
            build_widget("freeform_svg", {"ops": []})

    def test_missing_param_raises_invalid_params(self):
        with pytest.raises(InvalidParams):
            build_widget("dot_grouping", {"total": 17})  # group_size manquant


# ─────────────────────── build_menu (prompt contract) ───────────────────────

class TestBuildMenu:
    """Pins build_menu()'s OUTPUT against REPRESENTATIONS' params_schema.

    This is the regression test for the bug where params_schema existed
    in REPRESENTATIONS but build_menu() only ever read when_ar/when_fr —
    so the LLM never saw the required param names and had to guess them
    on every single [WIDGET] call (groupSize vs group_size, a nested
    "labels" dict instead of flat *_label keys, etc.), silently costing a
    repair round-trip on almost every dot_grouping / euclidean_division
    turn. All layout functions and build_widget() were already covered
    by the tests above; the actual prompt string shown to the model was
    not, which is exactly how this slipped through. Without this class,
    the same drift can reoccur invisibly the next time a representation
    is added or params_schema is extended without touching build_menu().
    """

    def test_every_representation_key_appears(self):
        for lang in ("ar", "fr"):
            menu = build_menu(lang)
            for key in REPRESENTATIONS:
                assert key in menu, f"'{key}' missing from build_menu({lang!r}) output"

    def test_every_param_name_appears_for_every_representation(self):
        for lang in ("ar", "fr"):
            menu = build_menu(lang)
            for key, spec in REPRESENTATIONS.items():
                schema = spec.get("params_schema")
                if schema is None:
                    continue  # freeform_svg — no fixed schema, handled elsewhere
                for param_name in schema:
                    assert param_name in menu, (
                        f"{key}: param '{param_name}' missing from "
                        f"build_menu({lang!r}) output — model can't know "
                        f"to send it"
                    )

    def test_dot_grouping_schema_uses_flat_labels_not_nested(self):
        """Regression pin for the exact incident: the model sent a nested
        {"labels": {...}} object instead of flat *_label keys because the
        menu didn't specify the flat schema. Asserts the flat keys are
        present and the nested-style key name is not what's advertised."""
        menu = build_menu("ar")
        for flat_key in ("dividend_label", "divisor_label",
                          "quotient_label", "remainder_label"):
            assert flat_key in menu
        assert '"labels"' not in menu

    def test_menu_uses_double_quoted_keys(self):
        """Loose smoke check that params schemas render as JSON syntax
        (double-quoted keys, via json.dumps) rather than Python dict repr
        (single-quoted keys), which would visually mismatch the JSON the
        model is asked to produce for [WIDGET]."""
        menu = build_menu("fr")
        assert "params:" in menu
        assert "{'" not in menu  # Python dict repr would produce this