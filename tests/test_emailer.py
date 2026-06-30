from pathlib import Path

from app.delivery.emailer import build_email_html, build_email_notice


SAMPLE_DIGEST = """# Daily AI and Policy Digest

- Generated at: 2026-06-30T00:00:00Z
- Items: 2

## AI-Industry

- [OpenAI launches a new enterprise feature](https://example.com/openai-enterprise)
  Source: openai-news | Published: 2026-06-30T08:00:00 | Score: 26.0
  Tags: AI,Official-AI,enterprise
  Reason: priority=10, fresh, matched=openai,enterprise
  Summary: A new OpenAI release expands enterprise admin controls.

## Policy-Central

- [\u56fd\u52a1\u9662\u5173\u4e8e\u4fc3\u8fdb\u6570\u5b57\u7ecf\u6d4e\u53d1\u5c55\u7684\u901a\u77e5](https://example.com/policy)
  Source: gov-policy | Published: 2026-06-30T09:00:00 | Score: 23.0
  Tags: Central-Policy,Regulation
  Reason: priority=10, policy_bonus, matched=\u56fd\u52a1\u9662,\u901a\u77e5
  Summary: <a href="https://example.com/policy">\u653f\u7b56\u6458\u8981</a><font color="#666">\u6765\u6e90</font>
"""


def test_build_email_notice_reads_markdown(tmp_path: Path) -> None:
    digest_path = tmp_path / "daily_digest.md"
    digest_path.write_text(SAMPLE_DIGEST, encoding="utf-8")

    content = build_email_notice(digest_path)

    assert "Daily AI and Policy Digest" in content
    assert "Policy-Central" in content


def test_build_email_html_renders_friendly_html(tmp_path: Path) -> None:
    digest_path = tmp_path / "daily_digest.md"
    digest_path.write_text(SAMPLE_DIGEST, encoding="utf-8")

    html = build_email_html(digest_path, "Daily AI and Policy Digest")

    assert "<!doctype html>" in html
    assert "\u5feb\u901f\u5bfc\u822a" in html
    assert "AI-Industry" in html
    assert "Policy-Central" in html
    assert "OpenAI launches a new enterprise feature" in html
    assert "\u67e5\u770b\u539f\u6587" in html
    assert "font color" not in html
