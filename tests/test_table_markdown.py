"""Tables in a reply come out as Markdown tables, with their bold and code kept in the cells."""

from chatsherpa.base import BaseProvider


async def _markdown(html: str) -> str:
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(html)
        md = await page.evaluate(BaseProvider._MD_JS, await page.query_selector("#reply"))
        await browser.close()
    return md


async def test_table_becomes_a_markdown_table_with_code_and_bold_kept():
    html = """
    <div id="reply"><p>Here is the comparison.</p>
    <table>
      <thead><tr><th>Feature</th><th>Python</th><th>JavaScript (Node)</th><th>C#</th></tr></thead>
      <tbody>
        <tr><td><strong>Comments</strong></td><td><code># single line</code></td><td><code>// or /* */</code></td><td><code>// or /* */</code></td></tr>
        <tr><td><strong>Variables</strong></td><td><code>cheese = 0</code></td><td><code>let cheese = 0</code></td><td><code>int cheese = 0;</code> (type required)</td></tr>
      </tbody>
    </table></div>"""
    md = await _markdown(html)
    lines = md.split("\n")
    assert lines[0] == "Here is the comparison."
    assert lines[2] == "| Feature | Python | JavaScript (Node) | C# |"
    assert lines[3] == "| --- | --- | --- | --- |"
    assert lines[4] == "| **Comments** | `# single line` | `// or /* */` | `// or /* */` |"
    assert lines[5] == "| **Variables** | `cheese = 0` | `let cheese = 0` | `int cheese = 0;` (type required) |"


async def test_pipes_in_cells_are_escaped_and_short_rows_are_padded():
    html = """
    <div id="reply"><table>
      <tr><th>A</th><th>B</th><th>C</th></tr>
      <tr><td>x | y</td><td>only two</td></tr>
    </table></div>"""
    md = await _markdown(html)
    assert "| A | B | C |" in md
    assert "| x \\| y | only two |  |" in md
