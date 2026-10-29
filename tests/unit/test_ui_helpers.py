import pytest

from scripts.ui.planning import nearest_cell_index

# 80 px wide measure columns as in the Scenario Comparison grid: the cells at the left edge are partly scrolled out
EDGES = [-46, 34, 114, 194, 274]


def test_the_cell_under_the_wanted_column_is_found():
    assert nearest_cell_index(EDGES, target_center=114 + 40, width=80) == 2
    assert nearest_cell_index(EDGES, target_center=274 + 40, width=80) == 4


def test_small_offsets_are_tolerated():
    assert nearest_cell_index(EDGES, target_center=114 + 40 + 20, width=80) == 2  # 20 px off is under 0.6 of a column


def test_a_column_that_is_not_rendered_raises_instead_of_returning_a_neighbour():
    # two columns to the left of the first rendered cell (and one to the right of the last): the old nearest-cell logic returned a neighbour
    for target in (-46 - 80 + 40, -46 - 160 + 40, 274 + 80 + 40):
        with pytest.raises(LookupError, match="not rendered"):
            nearest_cell_index(EDGES, target_center=target, width=80)


def test_a_row_without_cells_raises():
    with pytest.raises(LookupError, match="no rendered cells"):
        nearest_cell_index([], target_center=0, width=80)
