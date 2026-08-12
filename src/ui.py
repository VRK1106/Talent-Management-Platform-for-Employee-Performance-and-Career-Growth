"""UI helpers: CSS injection and reusable styled HTML components.

This is the only module in ``src/`` allowed to call Streamlit. It keeps the
themed enterprise look consistent across every page.
"""

from __future__ import annotations

import html
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import streamlit as st

_ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
_CSS_PATH = _ASSETS_DIR / "styles.css"


@lru_cache(maxsize=1)
def _read_css() -> str:
    try:
        return _CSS_PATH.read_text(encoding="utf-8")
    except OSError:
        return ""


def load_css() -> None:
    """Inject the custom themed stylesheet. Call at the top of every page."""
    # Ensure default session state theme is set
    if "app_theme" not in st.session_state:
        st.session_state.app_theme = "Dark"

    css = _read_css()
    if css:
        st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)

    # Update active user's timestamp
    if st.session_state.get("authenticated", False) and st.session_state.get("user_info"):
        try:
            from src.users import get_db_connection, update_user_activity
            emp_id = st.session_state.user_info.get("employee_id")
            if emp_id:
                update_user_activity(emp_id)
        except Exception:
            pass

    # Dynamic Light Mode stylesheet overrides (active only when body has .ts-light-mode class)
    light_css = """
    <style>
    body.ts-light-mode {
      --ts-bg: #F3F4F6;
      --ts-surface: #FFFFFF;
      --ts-surface-solid: #FFFFFF;
      --ts-border: rgba(0, 0, 0, 0.08);
      --ts-border-hover: rgba(79, 70, 229, 0.35);
      --ts-primary: #4F46E5;
      --ts-primary-hover: #4338CA;
      --ts-secondary: #10B981;
      --ts-accent: #8B5CF6;
      --ts-text: #1F2937;
      --ts-text-secondary: #4B5563;
      --ts-text-heading: #111827;
      --ts-text-muted: #9CA3AF;
      --ts-popover-bg: #FFFFFF;
      --ts-popover-text: #1F2937;
      --ts-gradient: linear-gradient(135deg, var(--ts-primary) 0%, var(--ts-accent) 100%);
      --ts-gradient-hero: linear-gradient(135deg, #EEF2F6 0%, #FFFFFF 100%);
      --ts-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05), 0 2px 4px -2px rgba(0, 0, 0, 0.05);
      --ts-shadow-hover: 0 10px 15px -3px rgba(0, 0, 0, 0.08), 0 4px 6px -4px rgba(0, 0, 0, 0.08);
      --ts-shadow-glow: 0 0 15px rgba(79, 70, 229, 0.08);
      --ts-input-bg: #FFFFFF;
      --ts-inner-bg: #F9FAFB;
      --ts-badge-bg: rgba(79, 70, 229, 0.08);
      --ts-badge-border: rgba(79, 70, 229, 0.15);
    }
    
    body.ts-light-mode .stApp {
      background-color: var(--ts-bg) !important;
      color: var(--ts-text) !important;
    }
    
    body.ts-light-mode [data-testid="stSidebar"] {
      background-color: #f8fafc !important;
      border-right: 1px solid var(--ts-border) !important;
    }
    
    body.ts-light-mode [data-testid="stSidebarNavLink"] {
      background-color: rgba(0, 0, 0, 0.02) !important;
      border-color: rgba(0, 0, 0, 0.04) !important;
    }
    
    body.ts-light-mode [data-testid="stSidebarNavLink"]:hover {
      background-color: rgba(79, 70, 229, 0.08) !important;
      border-color: rgba(79, 70, 229, 0.2) !important;
    }
    
    body.ts-light-mode [data-testid="stSidebarNavLink"] span {
      color: var(--ts-text-secondary) !important;
    }
    
    body.ts-light-mode [data-testid="stSidebarNavLink"]:hover span {
      color: var(--ts-primary) !important;
    }
    
    body.ts-light-mode [data-testid="stSidebarNavLink"][aria-current="page"] span {
      color: #FFFFFF !important;
    }
    
    /* Text color overrides */
    body.ts-light-mode label, 
    body.ts-light-mode .stWidgetLabel, 
    body.ts-light-mode [data-testid="stWidgetLabel"], 
    body.ts-light-mode [data-testid="stWidgetLabel"] p, 
    body.ts-light-mode [data-testid="stWidgetLabel"] span,
    body.ts-light-mode [data-testid="stHeader"] p,
    body.ts-light-mode [data-testid="stMarkdownContainer"] p,
    body.ts-light-mode [data-testid="stMarkdownContainer"] li,
    body.ts-light-mode [data-testid="stMarkdownContainer"] span,
    body.ts-light-mode [data-testid="stExpander"] summary,
    body.ts-light-mode [data-testid="stExpander"] summary p,
    body.ts-light-mode [data-testid="stExpander"] p,
    body.ts-light-mode .stTextInput label, 
    body.ts-light-mode .stSlider label, 
    body.ts-light-mode .stSelectbox label, 
    body.ts-light-mode .stMultiSelect label {
      color: var(--ts-text) !important;
    }
    
    body.ts-light-mode [data-testid="stCaptionContainer"],
    body.ts-light-mode [data-testid="stCaptionContainer"] p,
    body.ts-light-mode [data-testid="stCaptionContainer"] span {
      color: var(--ts-text-secondary) !important;
    }
    
    body.ts-light-mode h1, 
    body.ts-light-mode h2, 
    body.ts-light-mode h3, 
    body.ts-light-mode h4, 
    body.ts-light-mode h5, 
    body.ts-light-mode h6,
    body.ts-light-mode [data-testid="stMarkdownContainer"] h1,
    body.ts-light-mode [data-testid="stMarkdownContainer"] h2,
    body.ts-light-mode [data-testid="stMarkdownContainer"] h3,
    body.ts-light-mode [data-testid="stMarkdownContainer"] h4 {
      color: var(--ts-text-heading) !important;
    }
    
    body.ts-light-mode div[data-testid="stTextInputRootElement"],
    body.ts-light-mode div[data-testid="stTextAreaRootElement"],
    body.ts-light-mode div[data-testid="stNumberInputRootElement"],
    body.ts-light-mode div[data-baseweb="base-input"],
    body.ts-light-mode div[data-baseweb="input"],
    body.ts-light-mode div[data-baseweb="textarea"] {
      background-color: var(--ts-input-bg) !important;
      border: 1px solid var(--ts-border) !important;
    }
    
    body.ts-light-mode .stTextInput input, 
    body.ts-light-mode .stTextArea textarea,
    body.ts-light-mode .stNumberInput input {
      color: var(--ts-text) !important;
      background-color: transparent !important;
      border: none !important;
    }
    
    body.ts-light-mode [data-baseweb="select"] {
      background-color: var(--ts-input-bg) !important;
      border: 1px solid var(--ts-border) !important;
    }
    body.ts-light-mode [data-baseweb="select"] > div {
      color: var(--ts-text) !important;
    }
    
    body.ts-light-mode div[data-baseweb="popover"],
    body.ts-light-mode div[data-baseweb="popover"] > div,
    body.ts-light-mode div[data-baseweb="popover"] ul,
    body.ts-light-mode div[data-baseweb="popover"] [role="listbox"],
    body.ts-light-mode div[data-baseweb="popover"] [role="menu"],
    body.ts-light-mode [data-baseweb="menu"] {
      background-color: var(--ts-popover-bg) !important;
      border: 1px solid var(--ts-border) !important;
    }
    
    body.ts-light-mode div[data-baseweb="popover"] li,
    body.ts-light-mode div[data-baseweb="popover"] [role="option"],
    body.ts-light-mode [data-baseweb="menu"] li,
    body.ts-light-mode [data-baseweb="menu"] [role="option"] {
      color: var(--ts-popover-text) !important;
    }
    
    body.ts-light-mode div[data-baseweb="popover"] li:hover,
    body.ts-light-mode div[data-baseweb="popover"] [role="option"]:hover,
    body.ts-light-mode [data-baseweb="menu"] li:hover,
    body.ts-light-mode [data-baseweb="menu"] [role="option"]:hover {
      background-color: #f1f5f9 !important;
      color: var(--ts-primary) !important;
    }
    
    body.ts-light-mode [data-testid="stFileUploader"] {
      background: rgba(255, 255, 255, 0.8) !important;
      border-color: rgba(99, 102, 241, 0.3) !important;
    }
    
    body.ts-light-mode .ts-card {
      background: rgba(255, 255, 255, 0.75) !important;
      border: 1px solid rgba(0, 0, 0, 0.06) !important;
      box-shadow: 0 4px 20px rgba(0, 0, 0, 0.02) !important;
    }
    body.ts-light-mode .ts-hero {
      background: linear-gradient(135deg, rgba(99, 102, 241, 0.08) 0%, rgba(6, 182, 212, 0.08) 100%) !important;
      border: 1px solid rgba(99, 102, 241, 0.1) !important;
    }
    body.ts-light-mode .ts-hero-title {
      background: linear-gradient(135deg, var(--ts-primary) 0%, var(--ts-secondary) 100%) !important;
      -webkit-background-clip: text !important;
      -webkit-text-fill-color: transparent !important;
    }
    body.ts-light-mode .ts-hero-subtitle {
      color: #334155 !important;
    }
    body.ts-light-mode .ts-section-title {
      background: linear-gradient(135deg, var(--ts-primary) 0%, var(--ts-secondary) 100%) !important;
      -webkit-background-clip: text !important;
      -webkit-text-fill-color: transparent !important;
    }
    body.ts-light-mode .ts-section-subtitle, 
    body.ts-light-mode .ts-card-body, 
    body.ts-light-mode .ts-result-text, 
    body.ts-light-mode .ts-result-page {
      color: #475569 !important;
    }
    body.ts-light-mode .ts-highlight {
      background: rgba(99, 102, 241, 0.15) !important;
      color: #4f46e5 !important;
    }
    
    body.ts-light-mode .ts-metric {
      background: rgba(255, 255, 255, 0.85) !important;
      border: 1px solid rgba(0, 0, 0, 0.05) !important;
    }
    body.ts-light-mode .ts-metric-value {
      background: linear-gradient(135deg, var(--ts-primary) 0%, var(--ts-secondary) 100%) !important;
      -webkit-background-clip: text !important;
      -webkit-text-fill-color: transparent !important;
    }
    body.ts-light-mode .ts-metric-label {
      color: #475569 !important;
    }
    
    body.ts-light-mode .ts-result-head {
      border-bottom-color: rgba(0, 0, 0, 0.05) !important;
    }
    
    body.ts-light-mode .stSlider [data-baseweb="slider"] div[role="slider"] {
      background: var(--ts-primary) !important;
      border: 2px solid #ffffff !important;
    }
    body.ts-light-mode .stSlider [data-baseweb="slider"]>div>div {
      background: rgba(0, 0, 0, 0.08) !important;
    }
    
    body.ts-light-mode [data-testid="stExpander"] {
      background-color: rgba(255, 255, 255, 0.6) !important;
      border: 1px solid rgba(0, 0, 0, 0.05) !important;
    }
    body.ts-light-mode [data-testid="stExpander"] summary,
    body.ts-light-mode [data-testid="stExpander"] > details,
    body.ts-light-mode [data-testid="stExpander"] > details > summary {
      background-color: transparent !important;
    }
    
    body.ts-light-mode .ts-info-banner {
      background: rgba(99, 102, 241, 0.08) !important;
      border-left-color: var(--ts-primary) !important;
    }
    body.ts-light-mode .ts-info-title {
      color: #4f46e5 !important;
    }
    body.ts-light-mode .ts-info-body {
      color: #475569 !important;
    }

    /* Chat Input styling in light mode */
    body.ts-light-mode .stChatInputContainer,
    body.ts-light-mode .stChatFloatingInputContainer,
    body.ts-light-mode [data-testid="stChatInput"] {
      background-color: var(--ts-bg) !important;
      border: none !important;
    }
    body.ts-light-mode [data-testid="stChatInput"] > div {
      background-color: var(--ts-surface-solid) !important;
      border: 1px solid var(--ts-border) !important;
      border-radius: 12px !important;
    }
    body.ts-light-mode [data-testid="stChatInput"] textarea {
      color: var(--ts-text) !important;
      background-color: transparent !important;
    }
    body.ts-light-mode [data-testid="stChatInput"] textarea::placeholder {
      color: var(--ts-text-secondary) !important;
      opacity: 0.6 !important;
    }
    body.ts-light-mode [data-testid="stChatInput"] button {
      background-color: transparent !important;
      color: var(--ts-primary) !important;
      border: none !important;
      box-shadow: none !important;
    }
    body.ts-light-mode [data-testid="stChatInput"] button:hover {
      color: var(--ts-primary-hover) !important;
      background-color: transparent !important;
      transform: scale(1.05) !important;
    }

    /* Chat Message Bubbles styling in light mode */
    body.ts-light-mode [data-testid="stChatMessage"] {
      background-color: var(--ts-surface) !important;
      border: 1px solid var(--ts-border) !important;
      border-radius: var(--ts-radius) !important;
      box-shadow: var(--ts-shadow) !important;
    }
    body.ts-light-mode [data-testid="stChatMessage"] p,
    body.ts-light-mode [data-testid="stChatMessage"] li,
    body.ts-light-mode [data-testid="stChatMessage"] span,
    body.ts-light-mode [data-testid="stChatMessage"] div {
      color: var(--ts-text) !important;
    }

    /* Blockquotes in Light Mode (e.g. Sources Cited) */
    body.ts-light-mode blockquote {
      border-left: 3px solid var(--ts-primary) !important;
      color: var(--ts-text-secondary) !important;
      background: rgba(0, 0, 0, 0.02) !important;
      padding: 0.5rem 1rem !important;
      margin: 0.5rem 0 !important;
      border-radius: 4px !important;
    }

    /* Radio Buttons styling in light mode */
    body.ts-light-mode div[role="radiogroup"] label,
    body.ts-light-mode div[role="radiogroup"] label p,
    body.ts-light-mode div[role="radiogroup"] label span {
      color: var(--ts-text) !important;
    }

    /* Custom overrides for profile card and status in light mode */
    body.ts-light-mode .ts-sidebar-profile {
      background: rgba(0, 0, 0, 0.025) !important;
      border: 1px solid rgba(0, 0, 0, 0.05) !important;
    }
    body.ts-light-mode .ts-profile-name {
      color: #1F2937 !important;
    }
    body.ts-light-mode .ts-sidebar-health {
      background: rgba(0, 0, 0, 0.015) !important;
      border: 1px solid rgba(0, 0, 0, 0.04) !important;
    }
    body.ts-light-mode .stTabs [data-baseweb="tab-list"] {
      background-color: rgba(0, 0, 0, 0.02) !important;
      border: 1px solid rgba(0, 0, 0, 0.04) !important;
    }
    body.ts-light-mode .stTabs [data-baseweb="tab"] {
      color: var(--ts-text-secondary) !important;
    }
    body.ts-light-mode .stTabs [data-baseweb="tab"][aria-selected="true"] {
      background-color: #FFFFFF !important;
      color: var(--ts-primary) !important;
      border: 1px solid rgba(0, 0, 0, 0.08) !important;
      box-shadow: 0 4px 12px rgba(0, 0, 0, 0.05) !important;
    }
    body.ts-light-mode .stApp {
      background: #F3F4F6 !important;
    }
    body.ts-light-mode .stApp::before {
      background-image:
        linear-gradient(rgba(0, 0, 0, 0.01) 1px, transparent 1px),
        linear-gradient(90deg, rgba(0, 0, 0, 0.01) 1px, transparent 1px) !important;
    }
    </style>
    """
    st.markdown(light_css, unsafe_allow_html=True)

    # Sync class selection with Python st.session_state.app_theme
    # AND observe Streamlit native settings gear toggling theme changes
    python_theme = "light" if st.session_state.app_theme == "Light" else "dark"
    
    # Inject JS via same-origin sandboxed iframe component to bypass markdown HTML sanitization
    js_sync_script = f"""
    <script>
    (function() {{
        try {{
            const parentWin = window.parent;
            const parentDoc = parentWin.document;
            const body = parentDoc.body;
            
            function g() {{
                try {{
                    for (let i = 0; i < parentWin.localStorage.length; i++) {{
                        const k = parentWin.localStorage.key(i);
                        if (k && k.indexOf('stActiveTheme') === 0) {{
                            const s = parentWin.localStorage.getItem(k);
                            if (s) {{
                                const o = JSON.parse(s);
                                if (o && o.base) return o.base;
                            }}
                        }}
                    }}
                }} catch (e) {{}}
                return (parentWin.matchMedia && parentWin.matchMedia('(prefers-color-scheme: light)').matches) ? 'light' : 'dark';
            }}
            const p = '{python_theme}';
            
            // Apply theme class immediately to body of parent
            if (p === 'light') {{
                body.classList.add('ts-light-mode');
                body.classList.remove('ts-dark-mode');
            }} else {{
                body.classList.add('ts-dark-mode');
                body.classList.remove('ts-light-mode');
            }}
            
            // Sync parent localStorage to match Python theme state
            try {{
                for (let i = 0; i < parentWin.localStorage.length; i++) {{
                    const k = parentWin.localStorage.key(i);
                    if (k && k.indexOf('stActiveTheme') === 0) {{
                        parentWin.localStorage.setItem(k, JSON.stringify({{ base: p }}));
                    }}
                }}
                parentWin.localStorage.setItem('stActiveTheme', JSON.stringify({{ base: p }}));
                parentWin.localStorage.setItem('stActiveTheme-/-v2', JSON.stringify({{ base: p }}));
                parentWin.localStorage.setItem('stActiveTheme-/-v3', JSON.stringify({{ base: p }}));
            }} catch(e) {{}}
            
            // Set up a loop to watch for theme changes in parent's Streamlit settings
            if (!parentWin.tsThemeInterval) {{
                parentWin.tsThemeLast = p;
                parentWin.tsThemeInterval = parentWin.setInterval(function() {{
                    const c = g();
                    if (c !== parentWin.tsThemeLast) {{
                        parentWin.tsThemeLast = c;
                        if (c === 'light') {{
                            body.classList.add('ts-light-mode');
                            body.classList.remove('ts-dark-mode');
                        }} else {{
                            body.classList.add('ts-dark-mode');
                            body.classList.remove('ts-light-mode');
                        }}
                        const bt = Array.from(parentDoc.querySelectorAll('button')).find(function(btn) {{ return btn.textContent.includes('Switch to'); }});
                        if (bt) {{
                            bt.click();
                        }}
                    }}
                }}, 500);
            }} else {{
                parentWin.tsThemeLast = p;
            }}
        }} catch(err) {{
            console.error('Theme sync error:', err);
        }}
    }})();
    </script>
    """
    import streamlit.components.v1 as components
    components.html(js_sync_script, height=0, width=0)


def highlight_text(text: str, query: str) -> str:
    """Highlight keywords from the search query inside the text safely.

    Escapes the input text to prevent XSS, extracts significant keywords from the
    query, and wraps them in a styled highlight span.
    """
    if not query:
        return html.escape(text)

    # Escape HTML of the main text first to prevent HTML injection
    escaped_text = html.escape(text)

    # Extract keywords from the query
    raw_words = re.findall(r"\w+", query.lower())

    # Filter out common stopwords to avoid cluttering results
    stopwords = {
        "the", "a", "an", "is", "are", "was", "were", "of", "and", "or", "in",
        "to", "for", "with", "what", "where", "how", "why", "who", "which",
        "about", "this", "that", "these", "those", "it", "its", "at", "by", "on",
        "be", "been", "have", "has", "had", "do", "does", "did", "from"
    }
    keywords = [w for w in raw_words if len(w) > 2 and w not in stopwords]

    if not keywords:
        return escaped_text

    # Escape regex special characters in keywords
    escaped_keywords = [re.escape(k) for k in keywords]

    # Match keywords in the text (case-insensitive, respecting boundaries)
    pattern = re.compile(r"\b(" + "|".join(escaped_keywords) + r")\b", re.IGNORECASE)

    # Wrap matched term in HTML highlight tags
    def replace_match(m):
        return f'<span class="ts-highlight">{m.group(1)}</span>'

    return pattern.sub(replace_match, escaped_text)


def info_banner(title: str, body: str, tone: str = "info") -> None:
    """Render a polished informational banner with a visual accent."""
    tone_class = {
        "info": "ts-info-banner",
        "success": "ts-info-banner ts-info-success",
        "warning": "ts-info-banner ts-info-warning",
    }.get(tone, "ts-info-banner")
    st.markdown(
        f"""
        <div class="{tone_class}">
            <div class="ts-info-title">{html.escape(title)}</div>
            <div class="ts-info-body">{html.escape(body)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def pill_row(items: list[str]) -> None:
    """Render a compact row of feature pills."""
    chips = "".join(f'<span class="ts-pill">{html.escape(item)}</span>' for item in items)
    st.markdown(f'<div class="ts-chip-row">{chips}</div>', unsafe_allow_html=True)


def hero(title: str, subtitle: str) -> None:
    """Render the gradient hero header."""
    st.markdown(
        f"""
        <div class="ts-hero">
            <div class="ts-hero-title">{html.escape(title)}</div>
            <div class="ts-hero-subtitle">{html.escape(subtitle)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def section_header(title: str, subtitle: str | None = None) -> None:
    """Render a gradient-accented section header."""
    sub = f'<div class="ts-section-subtitle">{html.escape(subtitle)}</div>' if subtitle else ""
    st.markdown(
        f"""
        <div class="ts-section">
            <div class="ts-section-title">{html.escape(title)}</div>
            {sub}
        </div>
        """,
        unsafe_allow_html=True,
    )


def card(title: str, body: str, icon: str = "") -> None:
    """Render a card with an optional icon, title, and HTML body."""
    icon_html = f'<span class="ts-card-icon">{html.escape(icon)}</span>' if icon else ""
    title_html = (
        f'<div class="ts-card-title">{icon_html}{html.escape(title)}</div>' if title else ""
    )
    st.markdown(
        f"""
        <div class="ts-card">
            {title_html}
            <div class="ts-card-body">{body}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def metric_tile(label: str, value: str | int) -> None:
    """Render a metric tile with a left accent bar, big value, and small label."""
    st.markdown(
        f"""
        <div class="ts-metric">
            <div class="ts-metric-value">{html.escape(str(value))}</div>
            <div class="ts-metric-label">{html.escape(str(label))}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def score_badge(score: float) -> str:
    """Return HTML for a similarity pill, color-scaled by score.

    High (>=0.75) green, mid (0.5-0.75) blue, low (<0.5) gray.
    """
    if score >= 0.75:
        cls = "ts-badge-high"
    elif score >= 0.5:
        cls = "ts-badge-mid"
    else:
        cls = "ts-badge-low"
    return f'<span class="ts-badge {cls}">{score:.3f}</span>'


def result_card(source: str, page, score: float, text: str, query: str = "") -> None:
    """Render a single search result as a card with source/page/score and highlighted text."""
    display_text = highlight_text(text, query)
    st.markdown(
        f"""
        <div class="ts-card ts-result">
            <div class="ts-result-head">
                <span class="ts-result-source">📄 {html.escape(str(source))}</span>
                <span class="ts-result-page">Page {html.escape(str(page))}</span>
                {score_badge(score)}
            </div>
            <div class="ts-result-text">{display_text}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def empty_state(title: str, body: str, icon: str = "🗂️") -> None:
    """Render a friendly empty-state card."""
    st.markdown(
        f"""
        <div class="ts-card ts-empty">
            <div class="ts-empty-icon">{html.escape(icon)}</div>
            <div class="ts-empty-title">{html.escape(title)}</div>
            <div class="ts-empty-body">{html.escape(body)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_sidebar(index: dict[str, Any], show_chat_history: bool = False) -> None:
    """Render a fully customized, premium sidebar with profile card, health indicators, stats and theme controls."""
    import urllib.request
    
    with st.sidebar:
        # 1. Branding Header (Sticky logo)
        st.markdown(
            """
            <div class="ts-sidebar-header-sticky">
                <span style='font-size: 2.3rem;'>🚀</span>
                <div style='font-size: 1.4rem; font-weight: 800; background: linear-gradient(135deg, #60A5FA 0%, #A78BFA 50%, #34D399 100%); -webkit-background-clip: text; -webkit-text-fill-color: transparent; margin-top: 0.3rem;'>
                    Talent Sphere
                </div>
                <div style='font-size: 0.72rem; letter-spacing: 0.15em; color: var(--ts-text-secondary); text-transform: uppercase; font-weight: 700; margin-top: 0.2rem;'>
                    ELEVATE PLATFORM
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.divider()

        # 2. Premium User Profile Card
        if st.session_state.get("authenticated", False):
            user_info = st.session_state.get("user_info", {}) or {}
            name = st.session_state.get("current_user", "User")
            role = st.session_state.get("user_role", "trainee")
            domain = user_info.get("domain", "General")
            emp_id = user_info.get("employee_id", "demo")
            
            # Get initials for Avatar
            parts = name.split()
            initials = (parts[0][0] + parts[1][0]).upper() if len(parts) >= 2 else (parts[0][:2].upper() if parts else "US")
            
            # Progress calculation
            progress_pct = 0
            progress_text = ""
            if role == "admin":
                # For Admin, count completed assignments vs total assignments system-wide
                try:
                    import sqlite3
                    from src.users import get_db_connection, _DB_PATH
                    conn = get_db_connection(_DB_PATH)
                    c = conn.cursor()
                    c.execute("SELECT COUNT(*) FROM assignments")
                    total = c.fetchone()[0]
                    c.execute("SELECT COUNT(*) FROM assignments WHERE status = 'completed'")
                    completed = c.fetchone()[0]
                    conn.close()
                    if total > 0:
                        progress_pct = int((completed / total) * 100)
                    progress_text = f"System Completion: {progress_pct}% ({completed}/{total})"
                except Exception:
                    progress_pct = 100
                    progress_text = "System Operational"
            else:
                # For Trainee, count personal completed assignments vs total assigned
                try:
                    from src.exams import get_assignments_for_trainee
                    assignments = get_assignments_for_trainee(emp_id)
                    total = len(assignments)
                    completed = len([a for a in assignments if a["status"] == "completed"])
                    if total > 0:
                        progress_pct = int((completed / total) * 100)
                    progress_text = f"Exams Completed: {progress_pct}% ({completed}/{total})"
                except Exception:
                    progress_text = "No assigned courses"
            
            # Profile Card HTML
            st.markdown(
                f"""
                <div class="ts-sidebar-profile">
                    <div style="display: flex; align-items: center; gap: 0.8rem; margin-bottom: 0.6rem;">
                        <div class="ts-profile-avatar">{initials}</div>
                        <div style="flex: 1; min-width: 0;">
                            <div class="ts-profile-name">{html.escape(name)}</div>
                            <div class="ts-profile-details">{html.escape(domain.capitalize())} · <span class="ts-profile-badge">{role.upper()}</span></div>
                        </div>
                    </div>
                    <div style="margin-top: 0.6rem;">
                        <div style="display: flex; justify-content: space-between; font-size: 0.72rem; color: var(--ts-text-secondary); margin-bottom: 0.2rem;">
                            <span>{progress_text}</span>
                        </div>
                        <div class="ts-progress-bar-bg">
                            <div class="ts-progress-bar-fill" style="width: {progress_pct}%;"></div>
                        </div>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        st.divider()

        if st.session_state.get("authenticated", False):
            # Custom navigation block with nested Chat History
            st.markdown(
                """
                <style>
                .chat-history-sidebar-box {
                    padding-left: 1.2rem;
                    border-left: 2px solid var(--ts-border);
                    margin-left: 0.6rem;
                    margin-top: 0.25rem;
                    margin-bottom: 0.75rem;
                }
                </style>
                """,
                unsafe_allow_html=True
            )

            role = st.session_state.get("user_role", "trainee")
            emp_id = st.session_state.get("user_info", {}).get("employee_id", "demo")

            st.markdown("<div style='font-size: 0.72rem; color: var(--ts-text-secondary); text-transform: uppercase; font-weight: 700; letter-spacing: 0.05em; margin-bottom: 0.4rem;'>Hub</div>", unsafe_allow_html=True)
            st.page_link("pages/home.py", label="Dashboard", icon="📊")

            st.markdown("<div style='font-size: 0.72rem; color: var(--ts-text-secondary); text-transform: uppercase; font-weight: 700; letter-spacing: 0.05em; margin-top: 0.8rem; margin-bottom: 0.4rem;'>AI Workspace</div>", unsafe_allow_html=True)
            st.page_link("pages/6_🤖_AI_Assistant.py", label="AI Assistant", icon="🤖")

            # RENDER NESTED CHAT HISTORY HERE IF REQUESTED
            if show_chat_history:
                from src.chats import (
                    get_chat_sessions_for_user,
                    create_chat_session,
                    delete_chat_session,
                    rename_chat_session
                )
                import datetime
                user_sessions = get_chat_sessions_for_user(emp_id)

                st.markdown('<div class="chat-history-sidebar-box">', unsafe_allow_html=True)
                
                # New chat button
                if st.button("➕ New Chat Session", key="sidebar_new_chat_btn", use_container_width=True):
                    import uuid
                    new_id = str(uuid.uuid4())
                    chat_title = f"Chat {datetime.datetime.now().strftime('%b %d, %H:%M')}"
                    create_chat_session(new_id, emp_id, chat_title)
                    st.session_state.active_chat_session_id = new_id
                    if "renaming_session_id" in st.session_state:
                        st.session_state.renaming_session_id = None
                    st.rerun()
                    
                st.write("")
                
                # Inline rename form
                if st.session_state.get("renaming_session_id"):
                    rename_id = st.session_state.renaming_session_id
                    current_title = ""
                    for s in user_sessions:
                        if s["session_id"] == rename_id:
                            current_title = s["title"]
                            break
                    
                    st.markdown("<div style='font-size: 0.8rem; font-weight: 600; color: var(--ts-primary); margin-bottom: 0.2rem;'>✏️ Rename Session</div>", unsafe_allow_html=True)
                    new_title_val = st.text_input("New Title", value=current_title, key="sidebar_rename_title_input", label_visibility="collapsed")
                    c_save, c_cancel = st.columns(2)
                    with c_save:
                        if st.button("Save", key="sidebar_save_rename_btn", type="primary", use_container_width=True):
                            if new_title_val.strip():
                                rename_chat_session(rename_id, new_title_val.strip())
                                st.session_state.renaming_session_id = None
                                st.rerun()
                    with c_cancel:
                        if st.button("Cancel", key="sidebar_cancel_rename_btn", type="secondary", use_container_width=True):
                            st.session_state.renaming_session_id = None
                            st.rerun()
                    st.divider()
                
                # List of chat sessions
                if not user_sessions:
                    st.caption("No chat history found.")
                else:
                    for s in user_sessions:
                        col_sel, col_ren, col_del = st.columns([3.8, 1.1, 1.1])
                        with col_sel:
                            is_active = (s["session_id"] == st.session_state.active_chat_session_id)
                            lbl = f"⭐ {s['title'][:10]}" if is_active else f"💬 {s['title'][:10]}"
                            if len(s['title']) > 10:
                                lbl += "..."
                            if st.button(lbl, key=f"sidebar_sel_chat_{s['session_id']}", use_container_width=True, type="secondary"):
                                st.session_state.active_chat_session_id = s["session_id"]
                                st.rerun()
                        with col_ren:
                            if st.button("✏️", key=f"sidebar_ren_chat_{s['session_id']}", help="Rename chat session", use_container_width=True):
                                st.session_state.renaming_session_id = s["session_id"]
                                st.rerun()
                        with col_del:
                            if st.button("🗑️", key=f"sidebar_del_chat_{s['session_id']}", help="Delete chat session", use_container_width=True):
                                delete_chat_session(s["session_id"])
                                if st.session_state.active_chat_session_id == s["session_id"]:
                                    st.session_state.active_chat_session_id = None
                                if st.session_state.get("renaming_session_id") == s["session_id"]:
                                    st.session_state.renaming_session_id = None
                                st.rerun()
                                
                st.markdown('</div>', unsafe_allow_html=True)

            st.page_link("pages/2_🔍_Search.py", label="Knowledge Search", icon="🔍")

            if role == "admin":
                st.markdown("<div style='font-size: 0.72rem; color: var(--ts-text-secondary); text-transform: uppercase; font-weight: 700; letter-spacing: 0.05em; margin-top: 0.8rem; margin-bottom: 0.4rem;'>File Center</div>", unsafe_allow_html=True)
                st.page_link("pages/7_📄_Documents.py", label="Documents", icon="📄")
                st.page_link("pages/1_📥_Ingest.py", label="Document Ingestion", icon="📥")
                
                st.markdown("<div style='font-size: 0.72rem; color: var(--ts-text-secondary); text-transform: uppercase; font-weight: 700; letter-spacing: 0.05em; margin-top: 0.8rem; margin-bottom: 0.4rem;'>Management</div>", unsafe_allow_html=True)
                st.page_link("pages/3_👥_User_Management.py", label="User Management", icon="👥")
                st.page_link("pages/4_📝_Exams.py", label="Exams", icon="📝")
                st.page_link("pages/5_📢_Announcements.py", label="Announcements", icon="📢")
            else:
                st.markdown("<div style='font-size: 0.72rem; color: var(--ts-text-secondary); text-transform: uppercase; font-weight: 700; letter-spacing: 0.05em; margin-top: 0.8rem; margin-bottom: 0.4rem;'>File Center</div>", unsafe_allow_html=True)
                st.page_link("pages/7_📄_Documents.py", label="Documents", icon="📄")
                
                st.markdown("<div style='font-size: 0.72rem; color: var(--ts-text-secondary); text-transform: uppercase; font-weight: 700; letter-spacing: 0.05em; margin-top: 0.8rem; margin-bottom: 0.4rem;'>Learning</div>", unsafe_allow_html=True)
                st.page_link("pages/4_📝_Exams.py", label="Exams", icon="📝")
                st.page_link("pages/5_📢_Announcements.py", label="Announcements", icon="📢")

            st.divider()

        # 5. Theme Selector (Single-Click Easy Toggle)
        if "app_theme" not in st.session_state:
            st.session_state.app_theme = "Dark"

        st.markdown(
            "<div style='font-size: 0.72rem; color: var(--ts-text-secondary); text-transform: uppercase; font-weight: 700; letter-spacing: 0.05em; margin-bottom: 0.4rem;'>Workspace Theme</div>",
            unsafe_allow_html=True
        )
        is_dark = st.session_state.app_theme == "Dark"
        theme_btn_label = "☀️ Switch to Light" if is_dark else "🌙 Switch to Dark"
        
        if st.button(theme_btn_label, use_container_width=True, type="secondary"):
            st.session_state.app_theme = "Light" if is_dark else "Dark"
            st.rerun()

        st.divider()

        # 3. Platform Health Indicators
        # Check SQLite DB
        sqlite_ok = True
        try:
            import sqlite3
            from src.users import get_db_connection, _DB_PATH
            conn = get_db_connection(_DB_PATH)
            conn.execute("SELECT 1")
            conn.close()
        except Exception:
            sqlite_ok = False
            
        # Check ChromaDB
        chroma_ok = (index["total_chunks"] >= 0) if index else False
        
        # Check Ollama
        ollama_ok = False
        try:
            with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=0.3) as r:
                if r.status == 200:
                    ollama_ok = True
        except Exception:
            try:
                with urllib.request.urlopen("http://localhost:11434/", timeout=0.3) as r:
                    if r.status == 200:
                        ollama_ok = True
            except Exception:
                pass
                
        health_html = f"""
        <div class="ts-sidebar-health">
            <div style="font-size: 0.72rem; color: var(--ts-text-secondary); text-transform: uppercase; font-weight: 700; letter-spacing: 0.05em; margin-bottom: 0.6rem;">Platform Health</div>
            <div style="display: flex; flex-direction: column; gap: 0.45rem;">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <span style="font-size: 0.8rem; color: var(--ts-text-secondary);">SQLite Core</span>
                    <span class="ts-health-status ts-health-{'ok' if sqlite_ok else 'err'}">{'ONLINE' if sqlite_ok else 'OFFLINE'}</span>
                </div>
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <span style="font-size: 0.8rem; color: var(--ts-text-secondary);">Chroma VectorDB</span>
                    <span class="ts-health-status ts-health-{'ok' if chroma_ok else 'err'}">{'ONLINE' if chroma_ok else 'OFFLINE'}</span>
                </div>
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <span style="font-size: 0.8rem; color: var(--ts-text-secondary);">Ollama Qwen AI</span>
                    <span class="ts-health-status ts-health-{'ok' if ollama_ok else 'err'}">{'ONLINE' if ollama_ok else 'OFFLINE'}</span>
                </div>
            </div>
        </div>
        """
        st.markdown(health_html, unsafe_allow_html=True)
        
        st.divider()

        # 4. Database Statistics list
        st.markdown(
            "<div style='font-size: 0.72rem; color: var(--ts-text-secondary); text-transform: uppercase; font-weight: 700; letter-spacing: 0.05em; margin-bottom: 0.6rem;'>Vector Catalog Stats</div>",
            unsafe_allow_html=True
        )
        st.caption(f"📚 Documents Indexed: **{index['sources']}**")
        st.caption(f"🧩 Total Vector Chunks: **{index['total_chunks']}**")

        # 6. Logout
        if st.session_state.get("authenticated", False):
            st.divider()
            if st.button("🚪 Logout", use_container_width=True, type="secondary"):
                st.session_state.authenticated = False
                st.session_state.current_user = None
                st.session_state.user_role = None
                st.session_state.user_info = None
                st.rerun()

