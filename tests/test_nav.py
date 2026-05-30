import pytest


# Regression: ISSUE-002 — duplicate nav items on desktop (CSS specificity)
# Found by /qa on 2026-05-30
# Report: .gstack/qa-reports/qa-report-localhost-2026-05-30.md
#
# nav .nav-links a { display: inline-flex } (specificity 0,1,2) was beating
# .nav-more-mobile-link { display: none } (specificity 0,1,0), showing all
# "More" dropdown items twice in the desktop nav.
@pytest.mark.asyncio
async def test_nav_mobile_links_hidden_css_rule(client):
    resp = await client.get("/", headers={"Accept": "text/html"})
    assert resp.status_code == 200
    # The high-specificity rule must exist so it beats nav .nav-links a { display: inline-flex }
    assert "nav .nav-links a.nav-more-mobile-link" in resp.text
    # The old standalone (low-specificity) form must not appear without the nav prefix
    # i.e. no rule that starts with just ".nav-more-mobile-link" directly
    import re
    assert not re.search(r"(?<!\w)\.nav-more-mobile-link\s*\{", resp.text)
