"""The signed-out page's look: wallpaper, True logo bar, red accents.

EVERYTHING IS EMBEDDED, NOTHING IS FETCHED. The wallpaper was served as a file
through Streamlit's static route, which Community Cloud answers from the app
server itself: ~0.45s to first byte, ~0.9s to download, and no Cache-Control,
so the browser revalidated it on every visit. It also could not START until
Streamlit had booted, connected, run the script and applied the CSS -- which is
why it visibly filled in top-down over a flat purple placeholder. A 33 KB WebP
inlined as a data URI arrives in the same message as the CSS that uses it.

SCOPED BY :has(), NOT BY WHEN THE STYLE IS REMOVED. Every rule is conditional
on `.st-key-login_card` being in the DOM. Streamlit keeps a style-only element
alive until the NEXT run finishes, so a rule that was merely "rendered on the
sign-in page" outlived it: after signing in, the wallpaper sat behind the lead
view for as long as that first data load took. Tied to the card instead, it
disappears the moment the card does.

Red lives only inside the login card, which is what was asked. The app-wide
accent is still `primaryColor` in config.toml; changing that one value would
make the whole app red instead.
"""
from __future__ import annotations

import base64
from functools import lru_cache
from pathlib import Path

ASSETS = Path(__file__).resolve().parent.parent / "assets"

TRUE_RED = "#ec2127"        # sampled from the True logo
# The logo red gives white button text 4.36:1 -- under the 4.5:1 WCAG AA minimum
# for text this size. One step deeper clears it at 5.2:1 and still reads as the
# same red; the accents that carry no text keep the exact brand value.
BUTTON_RED = "#d71920"
BUTTON_RED_HOVER = "#b8141a"

# Sampled left to right across the wallpaper at 40% height. Painted beneath
# the image so that the frame before it decodes, or a browser that refuses the
# data URI, still shows the right colours rather than a flat block.
GRADIENT = "linear-gradient(90deg, #e72921 0%, #cf214f 25%, #a5308a 50%, #4863bd 75%, #068ee3 100%)"

CARD = ".st-key-login_card"
ON_LOGIN = f".stApp:has({CARD})"


def _data_uri(name: str, mime: str) -> str:
    return f"data:{mime};base64," + base64.b64encode((ASSETS / name).read_bytes()).decode()


@lru_cache(maxsize=1)
def login_css() -> str:
    """Built once per process; the assets never change while it runs."""
    bg = _data_uri("login_background.webp", "image/webp")
    logo = _data_uri("true_logo.png", "image/png")
    return f"""<style>
  {ON_LOGIN} {{
    background: url("{bg}") center / cover no-repeat, {GRADIENT};
  }}
  {ON_LOGIN} [data-testid="stHeader"] {{ background: transparent; }}

  /* White top bar with the logo. A pseudo-element, so there is no extra DOM
     for Streamlit to lay out, and it vanishes with the card like the rest.
     Kept one z-index step under Streamlit's own header so the app toolbar
     stays clickable when the app is opened directly rather than in the shell. */
  {ON_LOGIN}::before {{
    content: ""; position: fixed; top: 0; left: 0; right: 0; height: 64px;
    background: #fff url("{logo}") no-repeat 32px 50% / auto 32px;
    box-shadow: 0 1px 0 rgba(0, 0, 0, 0.06);
    z-index: 999989;
  }}

  /* The card: the form's dark text is unreadable straight on the gradient. */
  {CARD} {{
    background: rgba(255, 255, 255, 0.95);
    border-radius: 20px;
    padding: 2.25rem 2rem 1.75rem;
    box-shadow: 0 24px 64px rgba(20, 10, 40, 0.35);
    margin-top: calc(64px + 4vh);
  }}

  /* Red accents, inside the card only. */
  {CARD} [data-testid="stBaseButton-primaryFormSubmit"] {{
    background-color: {BUTTON_RED}; border-color: {BUTTON_RED}; color: #fff;
  }}
  {CARD} [data-testid="stBaseButton-primaryFormSubmit"]:hover,
  {CARD} [data-testid="stBaseButton-primaryFormSubmit"]:focus-visible {{
    background-color: {BUTTON_RED_HOVER}; border-color: {BUTTON_RED_HOVER}; color: #fff;
  }}
  {CARD} [data-testid="stBaseButton-secondaryFormSubmit"]:hover,
  {CARD} [data-testid="stBaseButton-secondaryFormSubmit"]:focus-visible {{
    border-color: {TRUE_RED}; color: {TRUE_RED};
  }}
  /* Selectors read off the rendered DOM of Streamlit 1.63, which draws tabs
     with react-aria: div[data-testid="stTab"], and the underline as an
     untagged child div of the selected tab. Older BaseWeb selectors
     (tab-highlight, button[role=tab]) match nothing here. The browser test
     asserts the computed colours, so a future upgrade that renames these
     fails loudly instead of quietly turning the accents teal again. */
  {CARD} [data-testid="stTab"][aria-selected="true"],
  {CARD} [data-testid="stTab"][aria-selected="true"] p,
  {CARD} [data-testid="stTab"]:hover p {{ color: {TRUE_RED}; }}
  {CARD} [data-testid="stTab"][aria-selected="true"] > div:not([data-testid]) {{
    background-color: {TRUE_RED};
  }}
  {CARD} [data-testid="stTextInputRootElement"]:focus-within {{ border-color: {TRUE_RED}; }}

  @media (max-width: 640px) {{
    {ON_LOGIN}::before {{ height: 52px; background-position: 16px 50%; background-size: auto 26px; }}
    {CARD} {{ margin-top: calc(52px + 1vh); padding: 1.5rem 1.25rem; }}
  }}
</style>"""
