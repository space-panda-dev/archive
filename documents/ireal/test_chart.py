"""The chart reader, on invented charts only."""

from __future__ import annotations

import unittest
from fractions import Fraction

from chart import Ending, Jump, Repeat, UnsupportedChart, read_chart


def chords(bar) -> list[str]:
    return [cell.text for _, cell in bar.chord_cells()]


class Bars(unittest.TestCase):
    def test_cells_shorthands_and_offsets(self) -> None:
        chart = read_chart("[T44*AC^7XyQ|A-7 D7LZG-7 C7 |FXyQKcl LZ")
        self.assertEqual([chords(bar) for bar in chart.bars], [["C^7"], ["A-7", "D7"], ["G-7", "C7"], ["F"], ["x"]])
        second = chart.bars[1]
        self.assertEqual(len(second.cells), 4)
        self.assertEqual([second.offset(index) for index, _ in second.chord_cells()], [0, Fraction(1, 2)])
        self.assertEqual(chart.bars[0].opens, "[")
        self.assertEqual(chart.bars[-1].cells[1].kind, "repeat_one")
        self.assertEqual([bar.number for bar in chart.bars], [1, 2, 3, 4, 5])
        self.assertEqual(chart.bars[0].mark, "A")
        self.assertTrue(all(bar.time == (4, 4) for bar in chart.bars))

    def test_commas_take_no_cell(self) -> None:
        bar = read_chart("T44C,F,G,C|").bars[0]
        self.assertEqual(len(bar.cells), 4)
        self.assertEqual(chords(bar), ["C", "F", "G", "C"])

    def test_time_signature_changes_hold_until_the_next(self) -> None:
        chart = read_chart("[T44C XyQ|T34F  |G  |T22C Z")
        self.assertEqual([bar.time for bar in chart.bars], [(4, 4), (3, 4), (3, 4), (2, 2)])
        self.assertEqual([bar.time_written for bar in chart.bars], [True, True, False, True])

    def test_cell_kinds_alternates_and_flags(self) -> None:
        chart = read_chart("[T44*iC7(Db7) W/E n |p F- (Gb7)  |sG7,lfC6   |r|XyQZ")
        first, second, third, fourth, fifth = chart.bars
        self.assertEqual([(cell.kind, cell.text, cell.alternate) for _, cell in first.chord_cells()],
                         [("chord", "C7", "Db7"), ("invisible", "W/E", None), ("no_chord", "n", None)])
        self.assertEqual([cell.kind for _, cell in second.chord_cells()], ["slash", "chord", "alternate"])
        self.assertEqual(second.chord_cells()[2][1].text, "Gb7")
        self.assertEqual([(cell.small, cell.fermata) for _, cell in third.chord_cells()],
                         [(True, False), (False, True)])
        self.assertEqual(fourth.cells[0].kind, "repeat_two")
        self.assertEqual(chords(fifth), [])   # the two-bar repeat's second bar
        self.assertEqual(first.mark, "i")

    def test_a_fermata_is_over_the_next_chord_or_else_the_last(self) -> None:
        bar = read_chart("T44f C G |").bars[0]
        self.assertEqual([cell.fermata for cell in bar.cells], [False, True, False, False, False])
        bar = read_chart("T44C G f |").bars[0]
        self.assertEqual([cell.fermata for cell in bar.cells], [False, False, True, False, False])

    def test_staff_text_without_its_offset(self) -> None:
        chart = read_chart("[T44C<*72Solos on cue> XyQ|F<Latin feel>XyQZ<Invented note>")
        self.assertEqual(chart.bars[0].texts, ["Solos on cue"])
        self.assertEqual(chart.comments, ((1, "Solos on cue"), (2, "Latin feel"), (2, "Invented note")))

    def test_spaces_between_bar_lines_are_not_a_bar(self) -> None:
        chart = read_chart("[T44C XyQ|G XyQ]   [D- XyQ|G7XyQZ ")
        self.assertEqual(len(chart.bars), 4)
        self.assertEqual(chart.bars[1].closes, "]")
        self.assertEqual(chart.bars[2].opens, "[")

    def test_a_custom_quality_is_still_one_chord(self) -> None:
        bar = read_chart("T44C*sparkle* G2 |").bars[0]
        self.assertEqual(chords(bar), ["C*sparkle*", "G2"])

    def test_end_of_playback(self) -> None:
        self.assertTrue(read_chart("T44C XyQ|G XyQUZ").bars[-1].end)


class Form(unittest.TestCase):
    def test_a_repeat_with_two_endings(self) -> None:
        chart = read_chart("{*AT44C XyQ|N1G7XyQ}  |N2C XyQ][*BF XyQZ")
        self.assertEqual(chart.repeats, (Repeat(1, 2, (Ending(1, 2, 2), Ending(2, 3, 3)), 2, None),))
        self.assertEqual(chart.bars[3].mark, "B")

    def test_three_endings_and_the_last_ends_at_the_next_section(self) -> None:
        chart = read_chart("{*AT44C XyQ|N1G XyQ}|N2D XyQ}|N3E XyQ|F XyQ|*BG XyQZ")
        repeat, = chart.repeats
        self.assertEqual(repeat.endings, (Ending(1, 2, 2), Ending(2, 3, 3), Ending(3, 4, 5)))
        self.assertEqual((repeat.first, repeat.last, repeat.times), (1, 3, 3))

    def test_a_plain_repeat_with_a_count(self) -> None:
        chart = read_chart("{T44C XyQ|G <3x>XyQ}[F XyQZ")
        self.assertEqual(chart.repeats, (Repeat(1, 2, (), 3, "3x"),))
        self.assertEqual(chart.comments, ())

    def test_dal_segno_al_coda(self) -> None:
        chart = read_chart("[T44*AC XyQ|SF XyQ|QG XyQ|C<D.S. al Coda> XyQ][QC XyQZ")
        self.assertEqual(chart.jumps, (Jump("dal_segno", "coda", 4, "D.S. al Coda"),))
        self.assertEqual((chart.segno, chart.coda_from, chart.coda), (2, 3, 5))

    def test_da_capo_al_fine(self) -> None:
        chart = read_chart("[T44C XyQ|G<Fine> XyQ|F XyQ|G <D.C. al Fine>XyQZ")
        self.assertEqual(chart.jumps, (Jump("da_capo", "fine", 4, "D.C. al Fine"),))
        self.assertEqual(chart.fine, 2)


class Unsupported(unittest.TestCase):
    CASES = {
        "no time signature before the first bar": "[C XyQZ",
        "unknown token 'H'": "[T44H XyQZ",
        "unknown time signature 'T99'": "[T99C XyQZ",
        "only N1 to N3": "{T44C |N0G }",
        "never closes": "{T44C XyQ|G XyQZ",
        "never opened": "[T44C XyQ|G XyQ}",
        "inside another": "{T44C |{G }}",
        "outside a repeat": "[T44C |N1G ]",
        "where N1 is due": "{T44C |N2G }",
        "no ending after it": "{T44C |N1G }[F Z",
        "cannot state: 'D.C. al 2nd ending'": "{T44C |N1G }|N2F <D.C. al 2nd ending>Z",
        "with no jump to a coda": "[T44QC |G |QF Z",
        "with no segno": "[T44C |G <D.S. al Fine>|F<Fine> Z",
        "staff text that does not close": "[T44C <Fine Z",
    }

    def test_structure_it_cannot_read_fails_naming_why(self) -> None:
        for reason, markup in self.CASES.items():
            with self.subTest(reason=reason):
                with self.assertRaisesRegex(UnsupportedChart, reason.replace(".", r"\.").replace("(", r"\(")):
                    read_chart(markup)

    def test_an_unknown_quality_is_not_a_structural_failure(self) -> None:
        self.assertEqual(chords(read_chart("T44C7b9b9b9 |").bars[0]), ["C7b9b9b9"])


if __name__ == "__main__":
    unittest.main()
