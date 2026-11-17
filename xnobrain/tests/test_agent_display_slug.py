"""Display-name identifiers preserve Vietnamese letters without accents."""

import unicodedata
import unittest

from xnobrain.services.agents import AgentsServiceMixin


class AgentDisplaySlugTests(unittest.TestCase):
    def test_vietnamese_names(self):
        cases = {
            "Biên tập & Kiểm soát chất lượng": "bien-tap-kiem-soat-chat-luong",
            "Chuyên viên Định giá": "chuyen-vien-dinh-gia",
            "HPG Analyst": "hpg-analyst",
        }
        for name, expected in cases.items():
            for form in ("NFC", "NFD"):
                with self.subTest(name=name, form=form):
                    self.assertEqual(
                        AgentsServiceMixin._agent_id_from_display_name(
                            unicodedata.normalize(form, name)
                        ),
                        expected,
                    )
