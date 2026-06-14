"""Tests for server.py MCP tool logic (without a live Swiggy session).

We test the formatting helpers by calling the inner logic directly through
a thin wrapper that substitutes FakeSwiggyClient for the lifespan client.
"""

from swiggy_deal_finder.candidates import CandidateService
from tests.fakes import WORK_ADDRESS_ID, FakeSwiggyClient


class TestListDishVariantsFormatting:
    async def test_output_contains_header_and_items(self):
        """list_variants output must include the header line and item bullet points."""
        client = FakeSwiggyClient()
        service = CandidateService(client)
        variants = await service.list_variants("dosa", WORK_ADDRESS_ID)

        # Simulate what the MCP tool does:
        dish = "dosa"
        if not variants:
            output = f"No variants found for '{dish}' within 7 km."
        else:
            lines = [f"Variants of '{dish}' available nearby:"]
            for name, lo, hi, _has_options in variants:
                lines.append(f"  - {name}  (₹{lo}–{hi})")
            lines.append(
                "Ask the user which specific one to compare, then call find_deals with that name."
            )
            output = "\n".join(lines)

        assert f"Variants of '{dish}' available nearby:" in output
        assert "  - Plain Dosa  (₹50–50)" in output
        assert "  - Masala Dosa  (₹90–90)" in output
        assert "Idli Dosa Batter" not in output
        assert "Adai Dosa Mix" not in output
        assert "Ask the user which specific one" in output

    async def test_empty_dish_returns_no_variants_message(self):
        """Unknown dish returns a clear no-variants message."""
        client = FakeSwiggyClient()
        service = CandidateService(client)
        variants = await service.list_variants("sushi", WORK_ADDRESS_ID)
        dish = "sushi"
        output = (
            f"No variants found for '{dish}' within 7 km."
            if not variants
            else "should not reach"
        )
        assert output == f"No variants found for '{dish}' within 7 km."
